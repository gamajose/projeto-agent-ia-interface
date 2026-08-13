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
Atualiza o código no WSL/Linux e prepara toda a pilha de IA.

Uso:
  bash scripts/update_wsl.sh [opções repassadas ao setup_ai_stack.sh]

O script aborta quando existem alterações locais. Ele nunca executa reset, clean
ou descarte automático de arquivos. Depois da atualização, reinicia web e worker
quando os serviços systemd existem, recarregando código e .env nos dois processos.
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

service_exists() {
  systemctl cat "$1" >/dev/null 2>&1
}

read_ui_port() {
  local env_file="${AGENT_ENV_FILE:-$PROJECT_DIR/.env}" value=""
  if [[ -f "$env_file" ]]; then
    value="$(awk -F= '$1=="AGENT_UI_PORT" {gsub(/["\r]/, "", $2); print $2; exit}' "$env_file")"
  fi
  [[ "$value" =~ ^[0-9]+$ ]] || value=8080
  printf '%s' "$value"
}

validate_web() {
  local port code="" elapsed=0
  port="$(read_ui_port)"
  while ((elapsed < 30)); do
    code="$(curl -sS --max-time 4 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/ui" 2>/dev/null || true)"
    if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
      ok "Interface respondeu em http://127.0.0.1:$port/ui"
      return
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  run_systemctl status agent-ia-web.service --no-pager -l || true
  fail "agent-ia-web está instalado, mas /ui não respondeu na porta $port"
}

restart_operational_services() {
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl não está disponível; reinicie manualmente web/worker se estiverem em execução"
    return
  fi

  if service_exists agent-ia-worker.service; then
    info "Reiniciando agent-ia-worker.service para recarregar código e credenciais"
    run_systemctl restart agent-ia-worker.service
    run_systemctl is-active --quiet agent-ia-worker.service \
      || fail "agent-ia-worker.service não permaneceu ativo após a atualização"
  else
    warn "agent-ia-worker.service não existe neste layout"
  fi

  if service_exists agent-ia-web.service; then
    info "Reiniciando agent-ia-web.service para publicar o checkout atualizado"
    run_systemctl restart agent-ia-web.service
    run_systemctl is-active --quiet agent-ia-web.service \
      || fail "agent-ia-web.service não permaneceu ativo após a atualização"
    validate_web
  else
    warn "agent-ia-web.service não existe neste layout; use bash scripts/start_web.sh se desejar iniciar manualmente"
  fi
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
restart_operational_services

ok "Atualização concluída"
grep '^version' "$PROJECT_DIR/pyproject.toml" || true
git rev-parse --short HEAD
