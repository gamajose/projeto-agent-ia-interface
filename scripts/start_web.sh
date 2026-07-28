#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
ENV_FILE="${AGENT_ENV_FILE:-$PROJECT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
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
