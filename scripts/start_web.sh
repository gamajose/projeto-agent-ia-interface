#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
VENV_DIR="${AGENT_VENV_DIR:-$HOME/.venvs/$PROJECT_NAME}"
AGENT_WEB="$VENV_DIR/bin/agent-web"

if [[ ! -x "$AGENT_WEB" ]]; then
    printf '[ERRO] Ambiente ainda não preparado: %s\n' "$VENV_DIR" >&2
    printf 'Execute primeiro: bash "%s/scripts/setup_wsl.sh"\n' "$PROJECT_DIR" >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec "$AGENT_WEB" "$@"
