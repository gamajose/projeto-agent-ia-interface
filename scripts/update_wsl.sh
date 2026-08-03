#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REMOTE="${AGENT_GIT_REMOTE:-origin}"
BRANCH="${AGENT_GIT_BRANCH:-main}"
SETUP_ARGS=()

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Atualiza o código no WSL e prepara toda a pilha de IA.

Uso:
  bash scripts/update_wsl.sh [opções repassadas ao setup_ai_stack.sh]

O script aborta quando existem alterações locais. Ele nunca executa reset, clean
ou descarte automático de arquivos. Depois da atualização, reinicia também o
worker operacional para que o novo código e o .env sejam realmente recarregados.
EOF
}

run_systemctl() {
  if ((EUID == 0)); then
    systemctl "$@"
  else
    command -v sudo >/dev/null 2>&1 || fail "sudo é necessário para reiniciar os serviços"
    sudo systemctl "$@"
  fi
}

restart_operational_worker() {
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl não está disponível; reinicie manualmente o worker operacional"
    return
  fi
  if ! systemctl cat agent-ia-worker.service >/dev/null 2>&1; then
    warn "agent-ia-worker.service não existe neste layout; nenhum worker de sistema foi reiniciado"
    return
  fi

  info "Reiniciando agent-ia-worker.service para recarregar código e credenciais SSH"
  run_systemctl restart agent-ia-worker.service
  run_systemctl is-active --quiet agent-ia-worker.service \
    || fail "agent-ia-worker.service não permaneceu ativo após a atualização"
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
restart_operational_worker

ok "Atualização concluída"
grep '^version' "$PROJECT_DIR/pyproject.toml" || true
git rev-parse --short HEAD
