#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REQUESTED_ROOT="${AGENT_INSTALL_ROOT:-}"
REQUESTED_APP="${AGENT_APP_DIR:-}"
REQUESTED_ENV="${AGENT_ENV_FILE:-}"
REQUESTED_VENV="${AGENT_VENV_DIR:-${AGENT_VENV:-}}"
OLLAMA_MODEL="${AGENT_OLLAMA_MODEL:-auto}"
INSTALL_OLLAMA=true
INSTALL_OMNIROUTE=true
INSTALL_OPENCODE=false
RESTART_WEB=true

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Instala e sincroniza a pilha de IA no Linux/WSL.

Preserva as chaves existentes, corrige caminhos do layout atual, instala Ollama
e sobe OmniRoute. O OpenCode pode ser incluído com --with-opencode.

Uso:
  bash scripts/setup_ai_stack.sh [opções]

Opções:
  --install-root CAMINHO
  --app-dir CAMINHO
  --env-file CAMINHO
  --venv-dir CAMINHO
  --ollama-model MODELO
  --without-ollama
  --without-omniroute
  --with-opencode
  --without-opencode
  --no-restart-web
  --help
EOF
}

while (($#)); do
  case "$1" in
    --install-root) shift; [[ $# -gt 0 ]] || fail "informe --install-root"; REQUESTED_ROOT="$1" ;;
    --app-dir) shift; [[ $# -gt 0 ]] || fail "informe --app-dir"; REQUESTED_APP="$1" ;;
    --env-file) shift; [[ $# -gt 0 ]] || fail "informe --env-file"; REQUESTED_ENV="$1" ;;
    --venv-dir) shift; [[ $# -gt 0 ]] || fail "informe --venv-dir"; REQUESTED_VENV="$1" ;;
    --ollama-model) shift; [[ $# -gt 0 ]] || fail "informe --ollama-model"; OLLAMA_MODEL="$1" ;;
    --without-ollama) INSTALL_OLLAMA=false ;;
    --without-omniroute) INSTALL_OMNIROUTE=false ;;
    --with-opencode) INSTALL_OPENCODE=true ;;
    --without-opencode) INSTALL_OPENCODE=false ;;
    --no-restart-web) RESTART_WEB=false ;;
    --help|-h) usage; exit 0 ;;
    *) fail "opção desconhecida: $1" ;;
  esac
  shift
done

TARGET_USER="${AGENT_TARGET_USER:-${SUDO_USER:-${USER:-$(id -un)}}}"
id "$TARGET_USER" >/dev/null 2>&1 || fail "usuário inexistente: $TARGET_USER"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

if [[ -n "$REQUESTED_APP" ]]; then
  APP_DIR="$(cd -- "$REQUESTED_APP" && pwd)"
elif [[ -n "$REQUESTED_ROOT" && -f "$REQUESTED_ROOT/app/pyproject.toml" ]]; then
  APP_DIR="$(cd -- "$REQUESTED_ROOT/app" && pwd)"
elif [[ -n "$REQUESTED_ROOT" && -f "$REQUESTED_ROOT/pyproject.toml" ]]; then
  APP_DIR="$(cd -- "$REQUESTED_ROOT" && pwd)"
else
  APP_DIR="$PROJECT_DIR"
fi
[[ -f "$APP_DIR/pyproject.toml" ]] || fail "pyproject.toml não encontrado em $APP_DIR"

if [[ -n "$REQUESTED_ROOT" ]]; then
  INSTALL_ROOT="$(mkdir -p "$REQUESTED_ROOT" && cd -- "$REQUESTED_ROOT" && pwd)"
elif [[ "$(basename "$APP_DIR")" == "app" && -d "$(dirname "$APP_DIR")/data" ]]; then
  INSTALL_ROOT="$(dirname "$APP_DIR")"
else
  INSTALL_ROOT="$APP_DIR"
fi

ENV_FILE="${REQUESTED_ENV:-$APP_DIR/.env}"
CONFIG_DIR="${AGENT_CONFIG_DIR:-$INSTALL_ROOT/config}"
DATA_DIR="${AGENT_DATA_DIR:-$INSTALL_ROOT/data}"
OMNIROUTE_ENV="$CONFIG_DIR/omniroute.env"

if [[ -n "$REQUESTED_VENV" ]]; then
  VENV_DIR="$REQUESTED_VENV"
elif [[ -x "$INSTALL_ROOT/venv/bin/python" ]]; then
  VENV_DIR="$INSTALL_ROOT/venv"
elif [[ -x "$TARGET_HOME/.venvs/$(basename "$APP_DIR")/bin/python" ]]; then
  VENV_DIR="$TARGET_HOME/.venvs/$(basename "$APP_DIR")"
elif [[ -x "$TARGET_HOME/.venvs/agent-ia/bin/python" ]]; then
  VENV_DIR="$TARGET_HOME/.venvs/agent-ia"
else
  VENV_DIR="$TARGET_HOME/.venvs/$(basename "$APP_DIR")"
fi

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || fail "sudo é necessário para instalar serviços"
  sudo -v
  SUDO=(sudo)
fi

as_target() {
  if ((EUID == 0)) && [[ "$(id -un)" != "$TARGET_USER" ]]; then
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$TARGET_USER" -- "$@"
    else
      sudo -u "$TARGET_USER" -H "$@"
    fi
  else
    "$@"
  fi
}

systemd_available() {
  command -v systemctl >/dev/null 2>&1 \
    && [[ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" == "systemd" ]]
}

read_env() {
  local key="$1" default="${2:-}" value=""
  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); gsub(/\r/, ""); print; exit}' "$ENV_FILE")"
    value="${value#\"}"; value="${value%\"}"
  fi
  printf '%s' "${value:-$default}"
}

write_env_value() {
  local key="$1" value="$2"
  "$VENV_DIR/bin/python" - "$ENV_FILE" "$key" "$value" <<'PY'
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
rendered = f"{key}={value}"
for index, raw in enumerate(lines):
    if raw.strip().startswith(f"{key}="):
        lines[index] = rendered
        break
else:
    lines.append(rendered)
fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(name, 0o600)
    os.replace(name, path)
finally:
    if os.path.exists(name): os.unlink(name)
PY
}

prepare_python_and_env() {
  info "Preparando Python e dependências em $VENV_DIR"
  as_target env AGENT_VENV_DIR="$VENV_DIR" bash "$APP_DIR/scripts/setup_wsl.sh"
  [[ -x "$VENV_DIR/bin/python" ]] || fail "virtualenv não foi criado em $VENV_DIR"

  "${SUDO[@]}" mkdir -p "$CONFIG_DIR" "$DATA_DIR"
  "${SUDO[@]}" chown -R "$TARGET_USER:$TARGET_GROUP" "$CONFIG_DIR" "$DATA_DIR"

  info "Sincronizando o .env sem sobrescrever chaves existentes"
  as_target "$VENV_DIR/bin/python" "$APP_DIR/scripts/sync_env_schema.py" \
    --env "$ENV_FILE" \
    --example "$APP_DIR/.env.example" \
    --install-root "$INSTALL_ROOT" \
    --app-dir "$APP_DIR" \
    --venv-dir "$VENV_DIR" \
    --omniroute-env "$OMNIROUTE_ENV"
  "${SUDO[@]}" chown "$TARGET_USER:$TARGET_GROUP" "$ENV_FILE"
  "${SUDO[@]}" chmod 600 "$ENV_FILE"
}

select_ollama_model() {
  if [[ -n "$OLLAMA_MODEL" && "$OLLAMA_MODEL" != "auto" ]]; then
    printf '%s' "$OLLAMA_MODEL"
    return
  fi
  local memory_kb
  memory_kb="$(awk '/MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || printf '0')"
  if [[ "$memory_kb" =~ ^[0-9]+$ ]] && ((memory_kb >= 7340032)); then
    printf '%s' "llama3.2:3b"
  else
    printf '%s' "llama3.2:1b"
  fi
}

install_ollama_stack() {
  systemd_available || fail "Ollama requer systemd ativo neste WSL"
  local installer model ollama_bin service_user service_group model_dir elapsed=0
  model="$(select_ollama_model)"
  model_dir="$DATA_DIR/ollama/models"

  if ! command -v ollama >/dev/null 2>&1; then
    info "Instalando Ollama pelo instalador oficial"
    installer="$(mktemp)"
    curl -fsSL "${OLLAMA_INSTALL_URL:-https://ollama.com/install.sh}" -o "$installer"
    "${SUDO[@]}" env OLLAMA_NO_START=1 sh "$installer"
    rm -f "$installer"
  else
    info "Ollama já está instalado"
  fi
  ollama_bin="$(command -v ollama)"
  [[ -x "$ollama_bin" ]] || fail "executável ollama não encontrado"

  if ! systemctl cat ollama.service >/dev/null 2>&1; then
    "${SUDO[@]}" tee /etc/systemd/system/ollama.service >/dev/null <<EOF
[Unit]
Description=Ollama local do Agent IA
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
ExecStart=$ollama_bin serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  fi

  service_user="$(systemctl show ollama.service -p User --value 2>/dev/null || true)"
  service_user="${service_user:-$TARGET_USER}"
  service_group="$(id -gn "$service_user" 2>/dev/null || printf '%s' "$TARGET_GROUP")"
  "${SUDO[@]}" mkdir -p "$model_dir" /etc/systemd/system/ollama.service.d
  "${SUDO[@]}" chown -R "$service_user:$service_group" "$DATA_DIR/ollama"
  "${SUDO[@]}" tee /etc/systemd/system/ollama.service.d/agent-ia.conf >/dev/null <<EOF
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MODELS=$model_dir"
EOF
  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable --now ollama.service

  while ((elapsed < 120)); do
    curl -fsS --max-time 4 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 3; elapsed=$((elapsed + 3))
  done
  ((elapsed < 120)) || fail "Ollama não respondeu em 127.0.0.1:11434"

  if ! OLLAMA_HOST=http://127.0.0.1:11434 ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Fxq "$model"; then
    info "Baixando modelo $model"
    OLLAMA_HOST=http://127.0.0.1:11434 ollama pull "$model"
  fi
  OLLAMA_HOST=http://127.0.0.1:11434 ollama show "$model" >/dev/null
  write_env_value OLLAMA_MODEL "$model"
  write_env_value OLLAMA_BASE_URL "http://127.0.0.1:11434"
  write_env_value OLLAMA_AUTO_FALLBACK "true"
  write_env_value OLLAMA_PREFERRED_MODELS "$model,llama3.2:1b,llama3.2:3b"
  ok "Ollama preparado com $model"
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    info "Instalando Docker"
    local installer
    installer="$(mktemp)"
    curl -fsSL "${DOCKER_INSTALL_URL:-https://get.docker.com}" -o "$installer"
    "${SUDO[@]}" sh "$installer"
    rm -f "$installer"
  fi
  if systemd_available; then
    "${SUDO[@]}" systemctl enable --now docker.service >/dev/null
  fi
  "${SUDO[@]}" docker info >/dev/null 2>&1 || fail "Docker não respondeu"
  if ! "${SUDO[@]}" docker compose version >/dev/null 2>&1; then
    fail "Docker Compose v2 não está disponível"
  fi
}

prepare_omniroute_env() {
  "${SUDO[@]}" mkdir -p "$(dirname "$OMNIROUTE_ENV")"
  "${SUDO[@]}" env OMNIROUTE_ENV_PATH="$OMNIROUTE_ENV" "$VENV_DIR/bin/python" - <<'PY'
from __future__ import annotations
import os
import re
import secrets
import tempfile
from pathlib import Path

path = Path(os.environ["OMNIROUTE_ENV_PATH"])
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
values: dict[str, str] = {}
positions: dict[str, int] = {}
for index, raw in enumerate(lines):
    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    key = key.strip(); value = value.strip().strip('"')
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        values[key] = value; positions[key] = index

updates = {
    "JWT_SECRET": values.get("JWT_SECRET") or secrets.token_urlsafe(48),
    "INITIAL_PASSWORD": values.get("INITIAL_PASSWORD") or secrets.token_urlsafe(24),
    "API_KEY_SECRET": values.get("API_KEY_SECRET") or secrets.token_urlsafe(48),
    "STORAGE_ENCRYPTION_KEY": values.get("STORAGE_ENCRYPTION_KEY") or secrets.token_urlsafe(48),
    "STORAGE_ENCRYPTION_KEY_VERSION": values.get("STORAGE_ENCRYPTION_KEY_VERSION") or "v1",
    "MACHINE_ID_SALT": values.get("MACHINE_ID_SALT") or secrets.token_urlsafe(32),
    "PORT": "20128",
    "NODE_ENV": "production",
    "HOSTNAME": "0.0.0.0",
    "DATA_DIR": "/app/data",
    "STORAGE_DRIVER": "sqlite",
    "APP_LOG_TO_FILE": "true",
    "AUTH_COOKIE_SECURE": "false",
    "REQUIRE_API_KEY": values.get("REQUIRE_API_KEY") or "false",
}
for key, value in updates.items():
    rendered = f"{key}={value}"
    if key in positions: lines[positions[key]] = rendered
    else: lines.append(rendered)
path.parent.mkdir(parents=True, exist_ok=True)
fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(name, 0o600); os.replace(name, path); os.chmod(path, 0o600)
finally:
    if os.path.exists(name): os.unlink(name)
PY
  "${SUDO[@]}" chown "$TARGET_USER:$TARGET_GROUP" "$OMNIROUTE_ENV"
  "${SUDO[@]}" chmod 600 "$OMNIROUTE_ENV"
}

install_omniroute_stack() {
  ensure_docker
  prepare_omniroute_env
  local port image elapsed=0 code=""
  port="$(read_env OMNIROUTE_PORT 20128)"
  image="$(read_env OMNIROUTE_IMAGE diegosouzapw/omniroute:latest)"
  info "Subindo OmniRoute ($image) em 127.0.0.1:$port"
  "${SUDO[@]}" env \
    OMNIROUTE_ENV_FILE="$OMNIROUTE_ENV" \
    docker compose --project-name agent-ia --env-file "$ENV_FILE" -f "$APP_DIR/docker-compose.yml" up -d omniroute

  while ((elapsed < 180)); do
    code="$(curl -sS --max-time 4 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/" 2>/dev/null || true)"
    [[ "$code" =~ ^[234][0-9][0-9]$ ]] && break
    sleep 3; elapsed=$((elapsed + 3))
  done
  ((elapsed < 180)) || fail "OmniRoute não respondeu na porta $port"

  write_env_value OMNIROUTE_ENV_FILE "$OMNIROUTE_ENV"
  write_env_value OMNIROUTE_BASE_URL "http://127.0.0.1:$port/v1"
  write_env_value OMNIROUTE_DEFAULT_ROUTE "$(read_env OMNIROUTE_DEFAULT_ROUTE auto/coding)"
  if [[ -z "$(read_env OMNIROUTE_API_KEY)" ]]; then
    write_env_value OMNIROUTE_API_KEY "$($VENV_DIR/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  fi
  ok "OmniRoute está disponível em 127.0.0.1:$port"
}

install_opencode_stack() {
  if [[ ! -x "$APP_DIR/scripts/setup_opencode.sh" ]]; then
    warn "setup_opencode.sh não foi encontrado; pulando OpenCode"
    return
  fi
  info "Instalando ou atualizando OpenCode"
  as_target env \
    AGENT_ENV_FILE="$ENV_FILE" \
    AGENT_VENV="$VENV_DIR" \
    AGENT_VENV_DIR="$VENV_DIR" \
    bash "$APP_DIR/scripts/setup_opencode.sh"
}

restart_web() {
  $RESTART_WEB || return
  if systemd_available && systemctl cat agent-ia-web.service >/dev/null 2>&1; then
    info "Reiniciando agent-ia-web.service"
    "${SUDO[@]}" systemctl restart agent-ia-web.service
    "${SUDO[@]}" systemctl is-active --quiet agent-ia-web.service \
      || fail "agent-ia-web.service não permaneceu ativo"
  else
    warn "agent-ia-web.service não existe; reinicie a interface com: bash $APP_DIR/scripts/start_web.sh"
  fi
}

show_provider_summary() {
  info "Chaves reconhecidas no .env (os valores não serão exibidos)"
  "$VENV_DIR/bin/python" - "$ENV_FILE" <<'PY'
from dotenv import dotenv_values
import sys
values = dotenv_values(sys.argv[1])
for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
    print(f"  {key}: {'configurada' if values.get(key) else 'ausente'}")
PY
  if [[ -x "$VENV_DIR/bin/agent" ]]; then
    as_target env AGENT_ENV_FILE="$ENV_FILE" "$VENV_DIR/bin/agent" doctor ai || true
  fi
}

prepare_python_and_env
$INSTALL_OLLAMA && install_ollama_stack
$INSTALL_OMNIROUTE && install_omniroute_stack
$INSTALL_OPENCODE && install_opencode_stack
restart_web
show_provider_summary

ok "Pilha de IA preparada"
printf '\nInterface: http://localhost:%s/ui\n' "$(read_env AGENT_UI_PORT 8080)"
