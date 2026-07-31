#!/usr/bin/env bash
set -Eeuo pipefail

trap 'status=$?; echo "Falha ao configurar o OpenCode na linha ${BASH_LINENO[0]} (código ${status})." >&2; exit "$status"' ERR

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

find_node_bin() {
  local direct candidate selected=""

  direct="$(command -v npm 2>/dev/null || true)"
  if [[ -n "${direct}" && -x "${direct}" ]] && command -v node >/dev/null 2>&1; then
    dirname "${direct}"
    return 0
  fi

  while IFS= read -r candidate; do
    [[ -x "${candidate}/npm" && -x "${candidate}/node" ]] || continue
    selected="${candidate}"
  done < <(find "${TARGET_HOME}/.nvm/versions/node" -mindepth 2 -maxdepth 2 -type d -name bin 2>/dev/null | sort -V)

  if [[ -n "${selected}" ]]; then
    printf '%s\n' "${selected}"
    return 0
  fi

  for candidate in \
    "${TARGET_HOME}/.local/bin" \
    "${TARGET_HOME}/bin" \
    "/usr/local/bin" \
    "/usr/bin"; do
    if [[ -x "${candidate}/npm" && -x "${candidate}/node" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

NODE_BIN_DIR="$(find_node_bin || true)"
if [[ -z "${NODE_BIN_DIR}" ]]; then
  cat >&2 <<EOF
npm e Node.js não foram encontrados para o usuário ${TARGET_USER}.
Instale Node.js 20 ou superior e execute novamente:
  bash ${ROOT_DIR}/scripts/setup_opencode.sh
EOF
  exit 1
fi

export PATH="${TARGET_HOME}/.local/bin:${NODE_BIN_DIR}:${PATH}"
hash -r

if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
  echo "Node.js/npm foram localizados, mas não puderam ser ativados no PATH: ${NODE_BIN_DIR}" >&2
  exit 1
fi

NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || printf '0')"
if [[ ! "${NODE_MAJOR}" =~ ^[0-9]+$ || "${NODE_MAJOR}" -lt 20 ]]; then
  echo "OpenCode requer Node.js 20 ou superior; encontrado: $(node --version 2>/dev/null || echo desconhecido)" >&2
  exit 1
fi

echo "Node.js detectado: $(node --version) em ${NODE_BIN_DIR}"
echo "Autorizando o postinstall do pacote opencode-ai..."
npm config set allow-scripts=opencode-ai --location=user >/dev/null

echo "Instalando ou atualizando OpenCode..."
npm install -g --allow-scripts=opencode-ai opencode-ai@latest
hash -r

NPM_PREFIX="$(npm prefix -g 2>/dev/null || true)"
CURRENT_OPENCODE="$(command -v opencode || true)"
for candidate in \
  "${CURRENT_OPENCODE}" \
  "${NPM_PREFIX:+${NPM_PREFIX}/bin/opencode}" \
  "${TARGET_HOME}/.local/bin/opencode"; do
  if [[ -n "${candidate}" && -x "${candidate}" ]]; then
    CURRENT_OPENCODE="$(readlink -f "${candidate}" 2>/dev/null || printf '%s' "${candidate}")"
    break
  fi
done

if [[ -z "${CURRENT_OPENCODE}" || ! -x "${CURRENT_OPENCODE}" ]]; then
  echo "O executável opencode não foi localizado após a instalação." >&2
  exit 1
fi

OPENCODE_VERSION="$(${CURRENT_OPENCODE} --version 2>&1 | head -n 1 || true)"
if [[ -z "${OPENCODE_VERSION}" ]]; then
  echo "O OpenCode foi localizado, mas não respondeu ao comando --version: ${CURRENT_OPENCODE}" >&2
  exit 1
fi

echo "OpenCode detectado: ${OPENCODE_VERSION} em ${CURRENT_OPENCODE}"

OPENCODE_BIN_DIR="$(dirname "${CURRENT_OPENCODE}")"
DETECTED_HOST="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
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
    "OPENCODE_SMALL_MODEL": existing.get("OPENCODE_SMALL_MODEL") or "auto/fast",
    "OPENCODE_DEFAULT_AGENT": existing.get("OPENCODE_DEFAULT_AGENT") or "plan",
    "OPENCODE_WEB_HOST": existing.get("OPENCODE_WEB_HOST") or "127.0.0.1",
    "OPENCODE_WEB_PORT": existing.get("OPENCODE_WEB_PORT") or "4096",
    "OPENCODE_WEB_URL": existing.get("OPENCODE_WEB_URL") or "http://127.0.0.1:4096",
    "OPENCODE_SERVER_USERNAME": existing.get("OPENCODE_SERVER_USERNAME") or "opencode",
    "OPENCODE_SERVER_PASSWORD": existing.get("OPENCODE_SERVER_PASSWORD") or os.environ["GENERATED_PASSWORD"],
    "OPENCODE_TUNNEL_HOST": existing.get("OPENCODE_TUNNEL_HOST") or os.environ["TUNNEL_HOST"],
    "OPENCODE_TUNNEL_SSH_PORT": existing.get("OPENCODE_TUNNEL_SSH_PORT") or os.environ["TUNNEL_SSH_PORT"],
    "OPENCODE_TUNNEL_USER": existing.get("OPENCODE_TUNNEL_USER") or os.environ["TUNNEL_USER"],
    "OPENCODE_INTERFACE_ENABLED": "true",
    "OPENCODE_INTERFACE_ALLOW_BUILD": "true",
    "OPENCODE_RUN_TIMEOUT_SECONDS": existing.get("OPENCODE_RUN_TIMEOUT_SECONDS") or "900",
    "OPENCODE_RUN_MAX_PROMPT_CHARS": existing.get("OPENCODE_RUN_MAX_PROMPT_CHARS") or "12000",
    "OPENCODE_RUN_MAX_OUTPUT_CHARS": existing.get("OPENCODE_RUN_MAX_OUTPUT_CHARS") or "250000",
    "OPENCODE_RUN_CONCURRENCY": existing.get("OPENCODE_RUN_CONCURRENCY") or "1",
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
PY

chmod 600 "${ENV_FILE}"
cd "${ROOT_DIR}"
AGENT_ENV_FILE="${ENV_FILE}" "${PYTHON_BIN}" -m app.services.opencode_cli --configure >/dev/null

if [[ ! -x "${SERVICE_BIN}" ]]; then
  echo "Entrada agent-opencode-web ainda não existe. Execute: ${VENV_DIR}/bin/pip install -e ${ROOT_DIR}" >&2
  exit 1
fi

SERVICE_FILE="/etc/systemd/system/opencode-web.service"
sudo -v
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
Environment=AGENT_ENV_FILE=${ENV_FILE}
Environment=PATH=${TARGET_HOME}/.local/bin:${NODE_BIN_DIR}:${OPENCODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
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
echo "Versão: ${OPENCODE_VERSION}"
echo "Workspace integrado: disponível no menu OpenCode do Agent IA"
echo "Interface original: http://127.0.0.1:4096"
echo "Usuário: opencode"
echo "Status: sudo systemctl status opencode-web --no-pager -l"
echo "Logs:   sudo journalctl -u opencode-web -f"
