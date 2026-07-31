#!/usr/bin/env bash

set -Eeuo pipefail

trap 'status=$?; echo "[opencode-deploy] ERRO na linha ${BASH_LINENO[0]} (código ${status})." >&2; exit "$status"' ERR

log() {
  printf '[opencode-deploy] %s\n' "$*"
}

fail() {
  printf '[opencode-deploy] ERRO: %s\n' "$*" >&2
  exit 1
}

APP_ROOT="${AGENT_APP_ROOT:-$HOME/agent-ia-production/current}"
ENV_FILE="${AGENT_ENV_FILE:-$HOME/.config/agent-ia/production.env}"
VENV_DIR="${AGENT_VENV:-$APP_ROOT/.venv}"
PYTHON_BIN="$VENV_DIR/bin/python"
HEALTH_URL="${AGENT_HEALTH_URL:-http://127.0.0.1:8080/health}"

[[ -d "$APP_ROOT" ]] || fail "release ativa não encontrada em $APP_ROOT"
[[ -f "$ENV_FILE" ]] || fail "arquivo de ambiente não encontrado em $ENV_FILE"
[[ -x "$PYTHON_BIN" ]] || fail "Python da release não encontrado em $PYTHON_BIN"

find_node_bin() {
  local direct candidate selected=""

  direct="$(command -v npm 2>/dev/null || true)"
  if [[ -n "$direct" && -x "$direct" ]] && command -v node >/dev/null 2>&1; then
    dirname "$direct"
    return 0
  fi

  while IFS= read -r candidate; do
    [[ -x "$candidate/npm" && -x "$candidate/node" ]] || continue
    selected="$candidate"
  done < <(find "$HOME/.nvm/versions/node" -mindepth 2 -maxdepth 2 -type d -name bin 2>/dev/null | sort -V)

  if [[ -n "$selected" ]]; then
    printf '%s\n' "$selected"
    return 0
  fi

  for candidate in "$HOME/.local/bin" "$HOME/bin" /usr/local/bin /usr/bin; do
    if [[ -x "$candidate/npm" && -x "$candidate/node" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

NODE_BIN_DIR="$(find_node_bin || true)"
[[ -n "$NODE_BIN_DIR" ]] || fail "Node.js e npm não foram encontrados para o usuário $(id -un)"
export PATH="$HOME/.local/bin:$NODE_BIN_DIR:$PATH"
hash -r

NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || printf '0')"
[[ "$NODE_MAJOR" =~ ^[0-9]+$ && "$NODE_MAJOR" -ge 20 ]] \
  || fail "OpenCode requer Node.js 20 ou superior; encontrado: $(node --version 2>/dev/null || echo desconhecido)"

log "Node.js detectado: $(node --version)"
log "autorizando postinstall do pacote opencode-ai"
npm config set allow-scripts=opencode-ai --location=user >/dev/null
log "instalando ou atualizando opencode-ai"
npm install -g --allow-scripts=opencode-ai opencode-ai@latest
hash -r

NPM_PREFIX="$(npm prefix -g 2>/dev/null || true)"
OPENCODE_BIN="$(command -v opencode || true)"
for candidate in \
  "$OPENCODE_BIN" \
  "${NPM_PREFIX:+${NPM_PREFIX}/bin/opencode}" \
  "$HOME/.local/bin/opencode"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    OPENCODE_BIN="$(readlink -f "$candidate" 2>/dev/null || printf '%s' "$candidate")"
    break
  fi
done

[[ -n "$OPENCODE_BIN" && -x "$OPENCODE_BIN" ]] \
  || fail "o executável opencode não foi localizado após a instalação"
OPENCODE_VERSION="$($OPENCODE_BIN --version 2>&1 | head -n 1 || true)"
[[ -n "$OPENCODE_VERSION" ]] || fail "o OpenCode não respondeu ao comando --version"
log "OpenCode detectado: $OPENCODE_VERSION em $OPENCODE_BIN"

export APP_ROOT ENV_FILE OPENCODE_BIN HOME
"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import os
import secrets
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
lines = path.read_text(encoding="utf-8").splitlines()
positions: dict[str, int] = {}
existing: dict[str, str] = {}
for index, raw in enumerate(lines):
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    positions[key] = index
    existing[key] = value.strip()

values = {
    "OPENCODE_ENABLED": "true",
    "OPENCODE_CLI_PATH": os.environ["OPENCODE_BIN"],
    "OPENCODE_WORKDIR": os.environ["APP_ROOT"],
    "OPENCODE_CONFIG_PATH": str(Path(os.environ["HOME"]) / ".config/opencode/opencode.json"),
    "OPENCODE_MODEL": existing.get("OMNIROUTE_DEFAULT_ROUTE") or "auto/coding",
    "OPENCODE_SMALL_MODEL": existing.get("OPENCODE_SMALL_MODEL") or "auto/fast",
    "OPENCODE_DEFAULT_AGENT": existing.get("OPENCODE_DEFAULT_AGENT") or "plan",
    "OPENCODE_WEB_HOST": existing.get("OPENCODE_WEB_HOST") or "127.0.0.1",
    "OPENCODE_WEB_PORT": existing.get("OPENCODE_WEB_PORT") or "4096",
    "OPENCODE_WEB_URL": existing.get("OPENCODE_WEB_URL") or "http://127.0.0.1:4096",
    "OPENCODE_SERVER_USERNAME": existing.get("OPENCODE_SERVER_USERNAME") or "opencode",
    "OPENCODE_SERVER_PASSWORD": existing.get("OPENCODE_SERVER_PASSWORD") or secrets.token_urlsafe(28),
    "OPENCODE_INTERFACE_ENABLED": "true",
    "OPENCODE_INTERFACE_ALLOW_BUILD": "true",
}

for key, value in values.items():
    rendered = f"{key}={value}"
    if key in positions:
        lines[positions[key]] = rendered
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(rendered)

path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY

cd "$APP_ROOT"
AGENT_ENV_FILE="$ENV_FILE" "$PYTHON_BIN" -m app.services.opencode_cli --configure >/dev/null

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
[[ -S "$XDG_RUNTIME_DIR/bus" ]] || fail "barramento systemd do usuário não está disponível"

systemctl --user restart agent-ia-api.service

for _ in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    log "API reiniciada e OpenCode disponível para a interface integrada"
    exit 0
  fi
  sleep 1
done

fail "a API não respondeu após ativar a configuração do OpenCode"
