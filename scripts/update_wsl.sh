#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REMOTE="${AGENT_GIT_REMOTE:-origin}"
BRANCH="${AGENT_GIT_BRANCH:-main}"
SETUP_ARGS=()

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Atualiza o código no WSL e prepara toda a pilha de IA.

Uso:
  bash scripts/update_wsl.sh [opções repassadas ao setup_ai_stack.sh]

O script aborta quando existem alterações locais. Ele nunca executa reset, clean
ou descarte automático de arquivos.
EOF
}

for argument in "$@"; do
  case "$argument" in
    --help|-h) usage; exit 0 ;;
  esac
done
SETUP_ARGS=("$@")

[[ -d "$PROJECT_DIR/.git" ]] || fail "$PROJECT_DIR não é um clone Git"
cd "$PROJECT_DIR"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  git status --short
  fail "há alterações locais; preserve-as em commit ou stash antes de atualizar"
fi

info "Atualizando $REMOTE/$BRANCH por fast-forward"
git fetch --prune "$REMOTE" "+refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH"
git switch "$BRANCH"
git branch --set-upstream-to="$REMOTE/$BRANCH" "$BRANCH" >/dev/null 2>&1 || true
git merge --ff-only "$REMOTE/$BRANCH"

info "Preparando dependências, provedores, Ollama e OmniRoute"
bash "$PROJECT_DIR/scripts/setup_ai_stack.sh" "${SETUP_ARGS[@]}"

ok "Atualização concluída"
grep '^version' "$PROJECT_DIR/pyproject.toml" || true
git rev-parse --short HEAD
