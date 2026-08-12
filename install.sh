#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${AGENT_REPO_URL:-https://github.com/gamajose/projeto-agent-ia-interface.git}"
REPO_REF="${AGENT_REPO_REF:-main}"
INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
APP_DIR=""
NON_INTERACTIVE=false
SKIP_DOCKER=false
OPENCODE_MODE="yes"
OLLAMA_MODE="${AGENT_INSTALL_OLLAMA:-true}"
OLLAMA_MODEL="${AGENT_OLLAMA_MODEL:-auto}"
PYTHON_BIN="${PYTHON_BIN:-}"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Instala o Agent IA em um caminho previsível no Linux ou WSL com systemd.

Uso local:
  bash install.sh [opções]

Uso em uma máquina nova:
  curl -fsSL https://raw.githubusercontent.com/gamajose/projeto-agent-ia-interface/main/install.sh | bash

Opções:
  --install-dir CAMINHO   raiz da instalação; padrão: /opt/agent-ia
  --ref REFERENCIA        branch ou tag; padrão: main
  --repo URL              repositório Git
  --non-interactive       não solicita dados opcionais
  --skip-docker           não instala Docker; exige Docker já funcional
  --with-opencode         prepara também o OpenCode integrado; padrão
  --without-opencode      não prepara o OpenCode
  --with-ollama           instala Ollama e um Llama local; padrão
  --without-ollama        não instala Ollama/modelo local
  --ollama-model MODELO   modelo local; padrão auto (1B ou 3B conforme RAM)
  --help                  mostra esta ajuda

Variáveis equivalentes:
  AGENT_INSTALL_ROOT, AGENT_REPO_URL, AGENT_REPO_REF, PYTHON_BIN,
  AGENT_INSTALL_OLLAMA e AGENT_OLLAMA_MODEL
EOF
}

while (($#)); do
  case "$1" in
    --install-dir) shift; [[ $# -gt 0 ]] || fail "informe o caminho após --install-dir"; INSTALL_ROOT="$1" ;;
    --repo) shift; [[ $# -gt 0 ]] || fail "informe a URL após --repo"; REPO_URL="$1" ;;
    --ref) shift; [[ $# -gt 0 ]] || fail "informe a referência após --ref"; REPO_REF="$1" ;;
    --non-interactive) NON_INTERACTIVE=true ;;
    --skip-docker) SKIP_DOCKER=true ;;
    --with-opencode) OPENCODE_MODE="yes" ;;
    --without-opencode) OPENCODE_MODE="no" ;;
    --with-ollama) OLLAMA_MODE="true" ;;
    --without-ollama) OLLAMA_MODE="false" ;;
    --ollama-model) shift; [[ $# -gt 0 ]] || fail "informe o modelo após --ollama-model"; OLLAMA_MODEL="$1" ;;
    --help|-h) usage; exit 0 ;;
    *) fail "opção desconhecida: $1" ;;
  esac
  shift
done

[[ "$INSTALL_ROOT" == /* ]] || fail "--install-dir precisa ser um caminho absoluto"
[[ "$INSTALL_ROOT" != *[[:space:]]* ]] || fail "--install-dir não pode conter espaços; use um caminho como /opt/agent-ia"
APP_DIR="$INSTALL_ROOT/app"
TARGET_USER="${SUDO_USER:-${USER:-$(id -un)}}"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" ]] || TARGET_HOME="$HOME"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$PWD}")" 2>/dev/null && pwd || printf '%s' "$PWD")"
SOURCE_ENV=""

if [[ -f "$SOURCE_DIR/pyproject.toml" && -d "$SOURCE_DIR/app" ]]; then
  [[ -f "$SOURCE_DIR/.env" ]] && SOURCE_ENV="$SOURCE_DIR/.env"
else
  SOURCE_DIR=""
fi

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || fail "sudo é necessário para instalar em $INSTALL_ROOT"
  sudo -v
  SUDO=(sudo)
fi

as_target() {
  if ((EUID == 0)) && [[ "$(id -un)" != "$TARGET_USER" ]]; then
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$TARGET_USER" -- "$@"
    elif command -v sudo >/dev/null 2>&1; then
      sudo -u "$TARGET_USER" -H "$@"
    else
      fail "não foi possível executar como $TARGET_USER; instale util-linux/runuser ou sudo"
    fi
  else
    "$@"
  fi
}

python_supported() {
  local candidate="$1"
  [[ -n "$candidate" ]] || return 1
  if [[ "$candidate" != */* ]]; then
    command -v "$candidate" >/dev/null 2>&1 || return 1
  else
    [[ -x "$candidate" ]] || return 1
  fi
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1
}

python_realpath() {
  "$1" -c 'import os,sys; print(os.path.realpath(sys.executable))' 2>/dev/null
}

select_supported_python() {
  local candidate uv_bin="$TARGET_HOME/.local/bin/uv" uv_candidate=""

  if [[ -n "$PYTHON_BIN" ]]; then
    if python_supported "$PYTHON_BIN"; then
      python_realpath "$PYTHON_BIN"
      return
    fi
    warn "PYTHON_BIN aponta para $($PYTHON_BIN --version 2>&1 || printf 'um Python incompatível'); o Agent IA exige Python 3.11.x. Ignorando esta versão."
    PYTHON_BIN=""
  fi

  for candidate in python3.11 /usr/bin/python3.11 /usr/local/bin/python3.11; do
    if python_supported "$candidate"; then
      python_realpath "$candidate"
      return
    fi
  done

  if [[ -x "$uv_bin" ]]; then
    uv_candidate="$(as_target env HOME="$TARGET_HOME" "$uv_bin" python find 3.11 2>/dev/null || true)"
    if [[ -n "$uv_candidate" ]] && python_supported "$uv_candidate"; then
      python_realpath "$uv_candidate"
      return
    fi
  fi

  return 1
}

install_bootstrap_packages() {
  local missing=()
  for command in git curl; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
  done
  ((${#missing[@]} == 0)) && return

  info "Instalando ferramentas básicas: ${missing[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    "${SUDO[@]}" dnf install -y git curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then
    "${SUDO[@]}" yum install -y git curl ca-certificates
  elif command -v zypper >/dev/null 2>&1; then
    "${SUDO[@]}" zypper --non-interactive install git curl ca-certificates
  else
    fail "gerenciador de pacotes não reconhecido; instale git e curl"
  fi
}

install_uv_python311() {
  local uv_bin="$TARGET_HOME/.local/bin/uv"

  info "Python 3.11.x não está disponível nos pacotes da distribuição; instalando runtime 3.11 gerenciado"
  as_target env HOME="$TARGET_HOME" UV_UNMANAGED_INSTALL="$TARGET_HOME/.local/bin" \
    sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  [[ -x "$uv_bin" ]] || fail "uv foi instalado, mas não ficou disponível em $uv_bin"
  as_target env HOME="$TARGET_HOME" "$uv_bin" python install 3.11
}

ensure_supported_python() {
  local selected=""
  selected="$(select_supported_python 2>/dev/null || true)"
  if [[ -n "$selected" ]]; then
    PYTHON_BIN="$selected"
    export PYTHON_BIN
    info "Python 3.11 detectado: $($PYTHON_BIN --version 2>&1)"
    return
  fi

  info "Python 3.11.x não encontrado; preparando uma versão paralela sem alterar o Python padrão do sistema"

  if command -v dnf >/dev/null 2>&1; then
    "${SUDO[@]}" dnf install -y python3.11 python3.11-pip python3.11-devel || true
  elif command -v yum >/dev/null 2>&1; then
    "${SUDO[@]}" yum install -y python3.11 python3.11-pip python3.11-devel || true
  elif command -v apt-get >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      python3.11 python3.11-venv python3.11-dev python3-pip || true
  elif command -v zypper >/dev/null 2>&1; then
    "${SUDO[@]}" zypper --non-interactive install python311 python311-pip python311-devel || true
  fi

  selected="$(select_supported_python 2>/dev/null || true)"
  if [[ -z "$selected" ]]; then
    install_uv_python311
    selected="$(select_supported_python 2>/dev/null || true)"
  fi

  [[ -n "$selected" ]] || fail "não foi possível disponibilizar Python 3.11.x nesta distribuição"
  PYTHON_BIN="$selected"
  export PYTHON_BIN
  info "Python selecionado para o Agent IA: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"
}

restore_mode_only_changes() {
  local path index_sha work_sha tracked_mode restored=0

  while IFS= read -r -d '' path; do
    [[ -f "$APP_DIR/$path" ]] || continue

    index_sha="$(as_target git -C "$APP_DIR" rev-parse ":$path" 2>/dev/null || true)"
    work_sha="$(as_target git -C "$APP_DIR" hash-object -- "$APP_DIR/$path" 2>/dev/null || true)"
    [[ -n "$index_sha" && "$index_sha" == "$work_sha" ]] || continue

    tracked_mode="$(as_target git -C "$APP_DIR" ls-files -s -- "$path" 2>/dev/null | awk 'NR==1 {print $1}')"
    case "$tracked_mode" in
      100755) "${SUDO[@]}" chmod 755 "$APP_DIR/$path" ;;
      100644) "${SUDO[@]}" chmod 644 "$APP_DIR/$path" ;;
      *) continue ;;
    esac
    restored=$((restored + 1))
  done < <(as_target git -C "$APP_DIR" diff --name-only -z --)

  if ((restored > 0)); then
    info "Permissões de $restored arquivo(s) restauradas conforme o Git"
  fi
}

repository_clean() {
  as_target git -C "$APP_DIR" diff --quiet -- \
    && as_target git -C "$APP_DIR" diff --cached --quiet --
}

port_available() {
  "$PYTHON_BIN" - "$1" <<'PY' >/dev/null 2>&1
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

read_ui_port() {
  local env_file="$APP_DIR/.env" value=""
  if [[ -f "$env_file" ]]; then
    value="$(awk -F= '$1=="AGENT_UI_PORT" {gsub(/["\r]/, "", $2); print $2; exit}' "$env_file")"
  fi
  [[ "$value" =~ ^[0-9]+$ ]] || value=8080
  printf '%s' "$value"
}

write_ui_port() {
  local port="$1" env_file="$APP_DIR/.env"
  "${SUDO[@]}" env AGENT_UI_ENV_FILE="$env_file" AGENT_UI_SELECTED_PORT="$port" \
    "$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

path = Path(os.environ["AGENT_UI_ENV_FILE"])
port = os.environ["AGENT_UI_SELECTED_PORT"]
path.parent.mkdir(parents=True, exist_ok=True)
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
positions: dict[str, int] = {}
for index, raw in enumerate(lines):
    if "=" not in raw or raw.lstrip().startswith("#"):
        continue
    key = raw.split("=", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        positions[key] = index

rendered = f"AGENT_UI_PORT={port}"
if "AGENT_UI_PORT" in positions:
    lines[positions["AGENT_UI_PORT"]] = rendered
else:
    lines.append(rendered)

fd, temporary = tempfile.mkstemp(prefix=".env.port.", dir=path.parent, text=True)
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
  "${SUDO[@]}" chown "$TARGET_USER:$TARGET_GROUP" "$env_file"
  "${SUDO[@]}" chmod 600 "$env_file"
}

prepare_ui_port() {
  local requested candidate limit
  "${SUDO[@]}" systemctl stop agent-ia-web.service >/dev/null 2>&1 || true
  requested="$(read_ui_port)"

  if port_available "$requested"; then
    return
  fi

  candidate=$((requested + 1))
  limit=$((requested + 20))
  while ((candidate <= limit && candidate <= 65535)); do
    if port_available "$candidate"; then
      warn "a porta $requested já está ocupada por outro processo; a interface usará $candidate"
      write_ui_port "$candidate"
      return
    fi
    candidate=$((candidate + 1))
  done

  fail "não foi encontrada uma porta livre entre $requested e $limit para a interface"
}

wait_ui() {
  local port elapsed=0 code
  port="$(read_ui_port)"
  while ((elapsed < 60)); do
    code="$(curl -sS --max-time 4 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/ui" 2>/dev/null || true)"
    if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
      info "Interface validada em http://127.0.0.1:$port/ui"
      return
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  "${SUDO[@]}" systemctl status agent-ia-web.service --no-pager -l || true
  fail "a interface não respondeu na porta $port após 60 segundos"
}

install_bootstrap_packages
ensure_supported_python
"${SUDO[@]}" mkdir -p "$INSTALL_ROOT"
"${SUDO[@]}" chown "$TARGET_USER:$TARGET_GROUP" "$INSTALL_ROOT"

if [[ -d "$APP_DIR/.git" ]]; then
  info "Instalação existente encontrada em $APP_DIR"
  restore_mode_only_changes
  if repository_clean; then
    as_target git -C "$APP_DIR" fetch --prune origin
    as_target git -C "$APP_DIR" checkout "$REPO_REF"
    if as_target git -C "$APP_DIR" show-ref --verify --quiet "refs/remotes/origin/$REPO_REF"; then
      as_target git -C "$APP_DIR" merge --ff-only "origin/$REPO_REF"
    fi
  else
    warn "há alterações locais reais em $APP_DIR; o código foi preservado sem atualizar"
    as_target git -C "$APP_DIR" status --short
  fi
elif [[ -e "$APP_DIR" ]]; then
  fail "$APP_DIR já existe, mas não é um clone Git válido"
else
  info "Baixando o projeto para $APP_DIR"
  as_target git clone --branch "$REPO_REF" --single-branch "$REPO_URL" "$APP_DIR"
fi

if [[ -n "$SOURCE_ENV" && ! -f "$APP_DIR/.env" ]]; then
  info "Migrando o .env da cópia local para a instalação padronizada"
  "${SUDO[@]}" install -o "$TARGET_USER" -g "$TARGET_GROUP" -m 600 "$SOURCE_ENV" "$APP_DIR/.env"
fi

prepare_ui_port

args=(--install-root "$INSTALL_ROOT" --user "$TARGET_USER")
$NON_INTERACTIVE && args+=(--non-interactive)
$SKIP_DOCKER && args+=(--skip-docker)
[[ "$OPENCODE_MODE" == "yes" ]] && args+=(--with-opencode)
[[ "$OPENCODE_MODE" == "no" ]] && args+=(--without-opencode)

bash "$APP_DIR/scripts/install_all.sh" "${args[@]}"

case "${OLLAMA_MODE,,}" in
  1|true|yes|sim|s)
    info "Preparando IA local com Ollama"
    bash "$APP_DIR/scripts/setup_ollama.sh" --install-root "$INSTALL_ROOT" --model "$OLLAMA_MODEL"
    ;;
  *)
    warn "instalação do Ollama foi desativada por configuração"
    ;;
esac

# Uma instalação anterior pode manter o processo web ativo no caminho antigo.
# Reiniciar somente esta unidade faz o systemd carregar WorkingDirectory,
# ExecStart, porta e configuração de IA da nova raiz.
info "Ativando a interface a partir de $APP_DIR"
"${SUDO[@]}" systemctl restart agent-ia-web.service
"${SUDO[@]}" systemctl is-active --quiet agent-ia-web.service \
  || fail "agent-ia-web não permaneceu ativo após a migração"
wait_ui
