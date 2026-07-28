#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_USER="${SUDO_USER:-${USER}}"
TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
ENV_FILE="${AGENT_ENV_FILE:-${ROOT_DIR}/.env}"
VENV_DIR="${AGENT_VENV:-${TARGET_HOME}/.venvs/projeto-agent-ia-interface}"
PYTHON_BIN="${VENV_DIR}/bin/python"
SERVICE_BIN="${VENV_DIR}/bin/agent-opencode-web"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Arquivo .env não encontrado: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Virtualenv não encontrado em ${VENV_DIR}. Execute antes: bash scripts/setup_wsl.sh" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm não encontrado. Instale o Node.js/NVM antes de continuar." >&2
  exit 1
fi

CURRENT_OPENCODE="$(command -v opencode || true)"
if [[ -z "${CURRENT_OPENCODE}" ]]; then
  echo "Instalando OpenCode..."
  npm install -g opencode-ai@latest
  CURRENT_OPENCODE="$(command -v opencode || true)"
else
  echo "Atualizando OpenCode existente..."
  npm install -g opencode-ai@latest
  CURRENT_OPENCODE="$(command -v opencode || true)"
fi

if [[ -z "${CURRENT_OPENCODE}" || ! -x "${CURRENT_OPENCODE}" ]]; then
  echo "O executável opencode não foi localizado após a instalação." >&2
  exit 1
fi

DETECTED_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
DETECTED_HOST="${DETECTED_HOST:-IP_DA_VM}"
GENERATED_PASSWORD="$("${PYTHON_BIN}" - <<'PY'
import secrets
print(secrets.token_urlsafe(28))
PY
)"

export ROOT_DIR TARGET_USER TARGET_HOME ENV_FILE CURRENT_OPENCODE DETECTED_HOST GENERATED_PASSWORD
"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
text = path.read_text(encoding="utf-8")
existing = {}
for raw in text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    existing[key.strip()] = value.strip()

values = {
    "OPENCODE_ENABLED": "true",
    "OPENCODE_CLI_PATH": os.environ["CURRENT_OPENCODE"],
    "OPENCODE_WORKDIR": os.environ["ROOT_DIR"],
    "OPENCODE_CONFIG_PATH": f'{os.environ["TARGET_HOME"]}/.config/opencode/opencode.json',
    "OPENCODE_MODEL": existing.get("OMNIROUTE_DEFAULT_ROUTE") or "auto/coding",
    "OPENCODE_DEFAULT_AGENT": "plan",
    "OPENCODE_WEB_HOST": "127.0.0.1",
    "OPENCODE_WEB_PORT": "4096",
    "OPENCODE_WEB_URL": "http://127.0.0.1:4096",
    "OPENCODE_SERVER_USERNAME": "opencode",
    "OPENCODE_SERVER_PASSWORD": os.environ["GENERATED_PASSWORD"],
    "OPENCODE_TUNNEL_HOST": os.environ["DETECTED_HOST"],
    "OPENCODE_TUNNEL_SSH_PORT": existing.get("OPENCODE_TUNNEL_SSH_PORT") or "22",
    "OPENCODE_TUNNEL_USER": os.environ["TARGET_USER"],
}

append = []
for key, value in values.items():
    if key not in existing:
        append.append(f"{key}={value}")

if append:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n# OpenCode via OmniRoute\n")
        handle.write("\n".join(append) + "\n")
PY

chmod 600 "${ENV_FILE}"
cd "${ROOT_DIR}"
"${PYTHON_BIN}" -m app.services.opencode_cli --configure >/dev/null

if [[ ! -x "${SERVICE_BIN}" ]]; then
  echo "Entrada agent-opencode-web ainda não existe. Rode novamente: bash scripts/setup_wsl.sh" >&2
  exit 1
fi

SERVICE_FILE="/etc/systemd/system/opencode-web.service"
sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=OpenCode Web via OmniRoute
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
Group=$(id -gn "${TARGET_USER}")
WorkingDirectory=${ROOT_DIR}
Environment=HOME=${TARGET_HOME}
ExecStart=${SERVICE_BIN}
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now opencode-web.service

echo
echo "OpenCode instalado: ${CURRENT_OPENCODE}"
echo "Interface: http://127.0.0.1:4096"
echo "Usuário: opencode"
if ! grep -q '^OPENCODE_SERVER_PASSWORD=' "${ENV_FILE}" || grep -q "^OPENCODE_SERVER_PASSWORD=${GENERATED_PASSWORD}$" "${ENV_FILE}"; then
  echo "Senha inicial: ${GENERATED_PASSWORD}"
else
  echo "A senha existente no .env foi preservada."
fi
echo
echo "Status: sudo systemctl status opencode-web --no-pager -l"
echo "Logs:   sudo journalctl -u opencode-web -f"
