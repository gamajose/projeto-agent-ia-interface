#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Execute este script como o usuário da aplicação, sem sudo. Ele solicitará sudo apenas para o serviço systemd." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_USER="${USER}"
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

echo "Instalando ou atualizando OpenCode..."
npm install -g opencode-ai@latest
CURRENT_OPENCODE="$(command -v opencode || true)"

if [[ -z "${CURRENT_OPENCODE}" || ! -x "${CURRENT_OPENCODE}" ]]; then
  echo "O executável opencode não foi localizado após a instalação." >&2
  exit 1
fi

OPENCODE_BIN_DIR="$(dirname "${CURRENT_OPENCODE}")"
if [[ ! -x "${OPENCODE_BIN_DIR}/node" ]] && ! command -v node >/dev/null 2>&1; then
  echo "Node.js não foi localizado para executar o OpenCode." >&2
  exit 1
fi

DETECTED_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
DETECTED_HOST="${DETECTED_HOST:-IP_DA_VM}"
TUNNEL_HOST="${OPENCODE_TUNNEL_HOST:-${DETECTED_HOST}}"
TUNNEL_SSH_PORT="${OPENCODE_TUNNEL_SSH_PORT:-22}"
TUNNEL_USER="${OPENCODE_TUNNEL_USER:-${TARGET_USER}}"
GENERATED_PASSWORD="$(${PYTHON_BIN} - <<'PY'
import secrets
print(secrets.token_urlsafe(28))
PY
)"

export ROOT_DIR TARGET_USER TARGET_HOME ENV_FILE CURRENT_OPENCODE GENERATED_PASSWORD
export TUNNEL_HOST TUNNEL_SSH_PORT TUNNEL_USER
"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import os
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
    "OPENCODE_CLI_PATH": os.environ["CURRENT_OPENCODE"],
    "OPENCODE_WORKDIR": os.environ["ROOT_DIR"],
    "OPENCODE_CONFIG_PATH": f'{os.environ["TARGET_HOME"]}/.config/opencode/opencode.json',
    "OPENCODE_MODEL": existing.get("OMNIROUTE_DEFAULT_ROUTE") or "auto/coding",
    "OPENCODE_SMALL_MODEL": "auto/fast",
    "OPENCODE_DEFAULT_AGENT": "plan",
    "OPENCODE_WEB_HOST": "127.0.0.1",
    "OPENCODE_WEB_PORT": "4096",
    "OPENCODE_WEB_URL": "http://127.0.0.1:4096",
    "OPENCODE_SERVER_USERNAME": "opencode",
    "OPENCODE_SERVER_PASSWORD": os.environ["GENERATED_PASSWORD"],
    "OPENCODE_TUNNEL_HOST": os.environ["TUNNEL_HOST"],
    "OPENCODE_TUNNEL_SSH_PORT": os.environ["TUNNEL_SSH_PORT"],
    "OPENCODE_TUNNEL_USER": os.environ["TUNNEL_USER"],
    "OPENCODE_INTERFACE_ENABLED": "true",
    "OPENCODE_INTERFACE_ALLOW_BUILD": "true",
    "OPENCODE_RUN_TIMEOUT_SECONDS": "900",
    "OPENCODE_RUN_MAX_PROMPT_CHARS": "12000",
    "OPENCODE_RUN_MAX_OUTPUT_CHARS": "250000",
    "OPENCODE_RUN_CONCURRENCY": "1",
}

append: list[str] = []
for key, value in values.items():
    if key not in positions:
        append.append(f"{key}={value}")
        continue
    if not existing.get(key):
        lines[positions[key]] = f"{key}={value}"

if append:
    if lines and lines[-1].strip():
        lines.append("")
    lines.append("# OpenCode via OmniRoute")
    lines.extend(append)

path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
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
Environment=PATH=${OPENCODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
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
echo "Workspace integrado: disponível no menu OpenCode do Agent IA"
echo "Interface original: http://127.0.0.1:4096"
echo "Usuário: opencode"
if grep -q "^OPENCODE_SERVER_PASSWORD=${GENERATED_PASSWORD}$" "${ENV_FILE}"; then
  echo "Senha inicial: ${GENERATED_PASSWORD}"
else
  echo "A senha existente no .env foi preservada."
fi
echo "Túnel: ssh -N -L 4096:127.0.0.1:4096 ${TUNNEL_USER}@${TUNNEL_HOST} -p ${TUNNEL_SSH_PORT}"
echo
echo "Status: sudo systemctl status opencode-web --no-pager -l"
echo "Logs:   sudo journalctl -u opencode-web -f"
