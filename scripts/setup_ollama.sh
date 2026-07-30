#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
REQUESTED_MODEL="${AGENT_OLLAMA_MODEL:-auto}"
INSTALL_URL="${OLLAMA_INSTALL_URL:-https://ollama.com/install.sh}"
OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Prepara o Ollama e um modelo Llama local para o Agent IA.

Uso:
  bash scripts/setup_ollama.sh [opções]

Opções:
  --install-root CAMINHO   raiz da instalação; padrão: /opt/agent-ia
  --model MODELO           modelo desejado ou auto
  --help                    mostra esta ajuda
EOF
}

while (($#)); do
  case "$1" in
    --install-root) shift; [[ $# -gt 0 ]] || fail "informe --install-root"; INSTALL_ROOT="$1" ;;
    --model) shift; [[ $# -gt 0 ]] || fail "informe --model"; REQUESTED_MODEL="$1" ;;
    --help|-h) usage; exit 0 ;;
    *) fail "opção desconhecida: $1" ;;
  esac
  shift
done

[[ "$INSTALL_ROOT" == /* ]] || fail "a raiz da instalação precisa ser absoluta"
[[ "$INSTALL_ROOT" != *[[:space:]]* ]] || fail "a raiz da instalação não pode conter espaços"

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || fail "sudo é necessário para instalar o Ollama"
  sudo -v
  SUDO=(sudo)
fi

DATA_DIR="$INSTALL_ROOT/data/ollama"
MODELS_DIR="$DATA_DIR/models"
MODEL_FILE="$INSTALL_ROOT/data/ollama.model"
ENV_FILE="$INSTALL_ROOT/app/.env"
VENV_PYTHON="$INSTALL_ROOT/venv/bin/python"
DROPIN_DIR="/etc/systemd/system/ollama.service.d"
DROPIN_FILE="$DROPIN_DIR/agent-ia.conf"

select_model() {
  local memory_kb
  if [[ -n "$REQUESTED_MODEL" && "$REQUESTED_MODEL" != "auto" ]]; then
    printf '%s' "$REQUESTED_MODEL"
    return
  fi

  memory_kb="$(awk '/MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || printf '0')"
  if [[ "$memory_kb" =~ ^[0-9]+$ ]] && ((memory_kb >= 7340032)); then
    printf '%s' "llama3.2:3b"
  else
    printf '%s' "llama3.2:1b"
  fi
}

ensure_disk_space() {
  local model="$1" free_kb required_kb
  free_kb="$(df -Pk "$INSTALL_ROOT" | awk 'NR==2 {print $4}')"
  free_kb="${free_kb:-0}"

  case "$model" in
    llama3.2:3b|llama3.2|*:3b) required_kb=4194304 ;;
    *) required_kb=2621440 ;;
  esac

  if [[ "$free_kb" =~ ^[0-9]+$ ]] && ((free_kb < required_kb)); then
    if [[ "$model" == "llama3.2:3b" || "$model" == "llama3.2" ]]; then
      warn "espaço insuficiente para $model; usando llama3.2:1b"
      SELECTED_MODEL="llama3.2:1b"
      required_kb=2621440
    fi
  fi

  if [[ "$free_kb" =~ ^[0-9]+$ ]] && ((free_kb < required_kb)); then
    fail "espaço livre insuficiente para baixar $SELECTED_MODEL; necessário aproximadamente $((required_kb / 1024)) MiB"
  fi
}

install_ollama() {
  local installer
  if command -v ollama >/dev/null 2>&1; then
    info "Ollama já instalado; preservando a instalação existente"
    return
  fi

  info "Instalando Ollama pelo instalador oficial"
  installer="$(mktemp)"
  trap 'rm -f "$installer"' RETURN
  curl -fsSL "$INSTALL_URL" -o "$installer"
  "${SUDO[@]}" env OLLAMA_NO_START=1 sh "$installer"
  rm -f "$installer"
  trap - RETURN
  command -v ollama >/dev/null 2>&1 || fail "o comando ollama não foi instalado"
}

ensure_service() {
  local ollama_bin service_user service_group
  ollama_bin="$(command -v ollama)"

  if ! systemctl cat ollama.service >/dev/null 2>&1; then
    service_user="${SUDO_USER:-${USER:-root}}"
    service_group="$(id -gn "$service_user")"
    info "Criando serviço systemd para o Ollama"
    "${SUDO[@]}" tee /etc/systemd/system/ollama.service >/dev/null <<EOF
[Unit]
Description=Ollama local do Agent IA
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$service_user
Group=$service_group
ExecStart=$ollama_bin serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  fi

  service_user="$(systemctl show ollama.service -p User --value 2>/dev/null || true)"
  service_user="${service_user:-root}"
  service_group="$(id -gn "$service_user" 2>/dev/null || printf 'root')"

  "${SUDO[@]}" mkdir -p "$MODELS_DIR" "$DROPIN_DIR"
  "${SUDO[@]}" chown -R "$service_user:$service_group" "$DATA_DIR"
  "${SUDO[@]}" chmod 750 "$DATA_DIR" "$MODELS_DIR"

  "${SUDO[@]}" tee "$DROPIN_FILE" >/dev/null <<EOF
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MODELS=$MODELS_DIR"
EOF

  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable ollama.service >/dev/null
  "${SUDO[@]}" systemctl restart ollama.service
}

wait_ollama() {
  local elapsed=0
  while ((elapsed < 120)); do
    if curl -fsS --max-time 4 "$OLLAMA_HOST_URL/api/tags" >/dev/null 2>&1; then
      ok "Ollama está respondendo em 127.0.0.1:11434"
      return
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done
  "${SUDO[@]}" systemctl status ollama.service --no-pager -l || true
  fail "Ollama não respondeu após 120 segundos"
}

pull_model() {
  if OLLAMA_HOST="$OLLAMA_HOST_URL" ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Fxq "$SELECTED_MODEL"; then
    info "Modelo local já disponível: $SELECTED_MODEL"
  else
    info "Baixando modelo local $SELECTED_MODEL"
    OLLAMA_HOST="$OLLAMA_HOST_URL" ollama pull "$SELECTED_MODEL"
  fi

  OLLAMA_HOST="$OLLAMA_HOST_URL" ollama show "$SELECTED_MODEL" >/dev/null 2>&1 \
    || fail "o modelo $SELECTED_MODEL não pôde ser validado"

  "${SUDO[@]}" mkdir -p "$(dirname "$MODEL_FILE")"
  printf '%s\n' "$SELECTED_MODEL" | "${SUDO[@]}" tee "$MODEL_FILE" >/dev/null
  "${SUDO[@]}" chmod 644 "$MODEL_FILE"
  ok "Llama local preparado: $SELECTED_MODEL"
}

update_agent_env() {
  [[ -x "$VENV_PYTHON" ]] || fail "Python do Agent não encontrado em $VENV_PYTHON"
  [[ -f "$ENV_FILE" ]] || fail "arquivo de ambiente não encontrado em $ENV_FILE"

  "${SUDO[@]}" env SELECTED_OLLAMA_MODEL="$SELECTED_MODEL" OLLAMA_ENV_FILE="$ENV_FILE" \
    "$VENV_PYTHON" - <<'PY'
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

path = Path(os.environ["OLLAMA_ENV_FILE"])
model = os.environ["SELECTED_OLLAMA_MODEL"]
updates = {
    "OLLAMA_MODEL": model,
    "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "OLLAMA_AUTO_FALLBACK": "true",
    "OLLAMA_PREFERRED_MODELS": f"{model},llama3.2:1b,llama3.2:3b",
}
lines = path.read_text(encoding="utf-8").splitlines()
positions: dict[str, int] = {}
for index, raw in enumerate(lines):
    if "=" not in raw or raw.lstrip().startswith("#"):
        continue
    key = raw.split("=", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        positions[key] = index

for key, value in updates.items():
    rendered = f"{key}={value}"
    if key in positions:
        lines[positions[key]] = rendered
    else:
        lines.append(rendered)

fd, temporary = tempfile.mkstemp(prefix=".env.ollama.", dir=path.parent, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
  ok "Agent configurado para usar $SELECTED_MODEL como IA local"
}

SELECTED_MODEL="$(select_model)"
ensure_disk_space "$SELECTED_MODEL"
install_ollama
ensure_service
wait_ollama
pull_model
update_agent_env
