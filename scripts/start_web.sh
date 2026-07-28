#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
ENV_FILE="${AGENT_ENV_FILE:-$PROJECT_DIR/.env}"
BOOTSTRAP_VENV_DIR="${AGENT_VENV_DIR:-$HOME/.venvs/$PROJECT_NAME}"
BOOTSTRAP_PYTHON="$BOOTSTRAP_VENV_DIR/bin/python"

if [[ -f "$ENV_FILE" ]]; then
    if [[ ! -x "$BOOTSTRAP_PYTHON" ]]; then
        printf '[ERRO] Ambiente ainda não preparado: %s\n' "$BOOTSTRAP_VENV_DIR" >&2
        printf 'Execute primeiro: bash "%s/scripts/setup_wsl.sh"\n' "$PROJECT_DIR" >&2
        exit 1
    fi

    while IFS= read -r -d '' assignment; do
        export "$assignment"
    done < <(
        "$BOOTSTRAP_PYTHON" - "$ENV_FILE" <<'PY'
from __future__ import annotations

import os
import re
import sys

from dotenv import dotenv_values

path = sys.argv[1]
valid_name = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

for key, value in dotenv_values(path).items():
    if value is None or not valid_name.fullmatch(key):
        continue
    os.write(1, f"{key}={value}".encode("utf-8") + b"\0")
PY
    )
fi

# Instalações npm dentro do NVM usam /usr/bin/env node no executável. Ao
# iniciar via systemd, inclua explicitamente a pasta do OpenCode no PATH.
if [[ -n "${OPENCODE_CLI_PATH:-}" ]]; then
    OPENCODE_BIN_DIR="$(dirname -- "$OPENCODE_CLI_PATH")"
    export PATH="$OPENCODE_BIN_DIR:$PATH"
fi

VENV_DIR="${AGENT_VENV_DIR:-$HOME/.venvs/$PROJECT_NAME}"
AGENT_WEB="$VENV_DIR/bin/agent-web"

if [[ ! -x "$AGENT_WEB" ]]; then
    printf '[ERRO] Ambiente ainda não preparado: %s\n' "$VENV_DIR" >&2
    printf 'Execute primeiro: bash "%s/scripts/setup_wsl.sh"\n' "$PROJECT_DIR" >&2
    exit 1
fi

cd "$PROJECT_DIR"
printf '[INFO] Iniciando interface em %s:%s\n' "${AGENT_UI_HOST:-127.0.0.1}" "${AGENT_UI_PORT:-8080}"
exec "$AGENT_WEB" "$@"
