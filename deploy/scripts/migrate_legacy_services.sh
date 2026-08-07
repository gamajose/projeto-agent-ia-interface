#!/usr/bin/env bash

set -Eeuo pipefail

MODE="${1:-}"
STATE_FILE="${2:-}"
readonly LEGACY_WEB="agent-ia-web.service"
readonly LEGACY_WORKER="agent-ia-worker.service"

log() { printf '[legacy-migration] %s\n' "$*"; }
fail() { printf '[legacy-migration] ERRO: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Uso:
  migrate_legacy_services.sh --stop ARQUIVO_ESTADO
  migrate_legacy_services.sh --restore ARQUIVO_ESTADO
  migrate_legacy_services.sh --disable ARQUIVO_ESTADO

Migra somente os serviços antigos agent-ia-web/agent-ia-worker. Os serviços de
infraestrutura (PostgreSQL/Redis/OmniRoute) permanecem ativos.
EOF
}

[[ "$MODE" =~ ^--(stop|restore|disable)$ ]] || { usage >&2; exit 2; }
[[ -n "$STATE_FILE" ]] || fail "informe o arquivo de estado"

privileged_systemctl() {
  if (( EUID == 0 )); then
    systemctl "$@"
    return
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n systemctl "$@"
    return
  fi
  fail "o runner precisa de permissão não interativa para gerenciar os serviços legados"
}

active() {
  systemctl is-active --quiet "$1" 2>/dev/null
}

enabled() {
  systemctl is-enabled --quiet "$1" 2>/dev/null
}

write_state() {
  local web_active="$1" worker_active="$2" web_enabled="$3" worker_enabled="$4"
  umask 077
  mkdir -p "$(dirname "$STATE_FILE")"
  cat >"$STATE_FILE" <<EOF
WEB_ACTIVE=$web_active
WORKER_ACTIVE=$worker_active
WEB_ENABLED=$web_enabled
WORKER_ENABLED=$worker_enabled
EOF
}

load_state() {
  [[ -f "$STATE_FILE" ]] || fail "estado da migração não encontrado: $STATE_FILE"
  # O arquivo é escrito exclusivamente por este script e contém somente 0/1.
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  : "${WEB_ACTIVE:=0}" "${WORKER_ACTIVE:=0}" "${WEB_ENABLED:=0}" "${WORKER_ENABLED:=0}"
  [[ "$WEB_ACTIVE" =~ ^[01]$ && "$WORKER_ACTIVE" =~ ^[01]$ ]] || fail "estado inválido"
  [[ "$WEB_ENABLED" =~ ^[01]$ && "$WORKER_ENABLED" =~ ^[01]$ ]] || fail "estado inválido"
}

stop_legacy() {
  local web_active=0 worker_active=0 web_enabled=0 worker_enabled=0
  active "$LEGACY_WEB" && web_active=1 || true
  active "$LEGACY_WORKER" && worker_active=1 || true
  enabled "$LEGACY_WEB" && web_enabled=1 || true
  enabled "$LEGACY_WORKER" && worker_enabled=1 || true
  write_state "$web_active" "$worker_active" "$web_enabled" "$worker_enabled"

  if (( web_active == 0 && worker_active == 0 )); then
    log "nenhum serviço legado ativo; nada precisa ser interrompido"
    return
  fi

  log "parando serviços legados antes de ocupar a porta e a fila com a nova release"
  (( worker_active == 1 )) && privileged_systemctl stop "$LEGACY_WORKER"
  (( web_active == 1 )) && privileged_systemctl stop "$LEGACY_WEB"

  if active "$LEGACY_WEB" || active "$LEGACY_WORKER"; then
    fail "um serviço legado permaneceu ativo após a solicitação de parada"
  fi
  log "serviços legados interrompidos com estado preservado para rollback"
}

restore_legacy() {
  load_state
  log "restaurando serviços legados após falha da nova release"
  if (( WEB_ENABLED == 1 )); then privileged_systemctl enable "$LEGACY_WEB" >/dev/null; fi
  if (( WORKER_ENABLED == 1 )); then privileged_systemctl enable "$LEGACY_WORKER" >/dev/null; fi
  if (( WORKER_ACTIVE == 1 )); then privileged_systemctl start "$LEGACY_WORKER"; fi
  if (( WEB_ACTIVE == 1 )); then privileged_systemctl start "$LEGACY_WEB"; fi
  log "restauração dos serviços legados concluída"
}

disable_legacy() {
  load_state
  if (( WEB_ENABLED == 1 || WORKER_ENABLED == 1 )); then
    log "desabilitando inicialização automática dos serviços legados já substituídos"
    privileged_systemctl disable "$LEGACY_WEB" "$LEGACY_WORKER" >/dev/null || true
  fi
  rm -f -- "$STATE_FILE"
  log "migração dos serviços legados concluída"
}

case "$MODE" in
  --stop) stop_legacy ;;
  --restore) restore_legacy ;;
  --disable) disable_legacy ;;
esac
