#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
APP_DIR="${AGENT_APP_DIR:-$INSTALL_ROOT/app}"
ENV_FILE="${AGENT_ENV_FILE:-$APP_DIR/.env}"
COMPOSE_FILE="${AGENT_COMPOSE_FILE:-$APP_DIR/docker-compose.yml}"
ACTION="${1:-status}"
SCOPE="${2:-all}"

info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[ERRO] %s\n' "$*" >&2; exit 1; }

[[ -f "$COMPOSE_FILE" ]] || fail "compose não encontrado: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || fail ".env não encontrado: $ENV_FILE"

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if ((EUID != 0)) && command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    fail "Docker não está disponível"
  fi
fi

COMPOSE=("${DOCKER[@]}" compose --project-name agent-ia --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

container_exists() {
  "${DOCKER[@]}" inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$("${DOCKER[@]}" inspect --format '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

ensure_container() {
  local service="$1"
  local name="$2"
  if container_exists "$name"; then
    if container_running "$name"; then
      info "Reutilizando container ativo: $name"
    else
      info "Iniciando container existente: $name"
      "${DOCKER[@]}" start "$name" >/dev/null
    fi
    return
  fi
  info "Criando serviço $service pelo Docker Compose"
  "${COMPOSE[@]}" up -d "$service"
}

stop_container() {
  local name="$1"
  local timeout="${2:-30}"
  container_exists "$name" || return 0
  container_running "$name" || return 0
  info "Parando $name"
  "${DOCKER[@]}" stop -t "$timeout" "$name" >/dev/null
}

start_scope() {
  case "$SCOPE" in
    infra)
      ensure_container postgres agent-ia-postgres
      ensure_container redis agent-ia-redis
      ;;
    omniroute)
      ensure_container omniroute omniroute
      ;;
    all)
      ensure_container postgres agent-ia-postgres
      ensure_container redis agent-ia-redis
      ensure_container omniroute omniroute
      ;;
    *) fail "escopo inválido: $SCOPE" ;;
  esac
}

stop_scope() {
  case "$SCOPE" in
    infra)
      stop_container agent-ia-redis 20
      stop_container agent-ia-postgres 30
      ;;
    omniroute)
      stop_container omniroute 40
      ;;
    all)
      stop_container omniroute 40
      stop_container agent-ia-redis 20
      stop_container agent-ia-postgres 30
      ;;
    *) fail "escopo inválido: $SCOPE" ;;
  esac
}

status_scope() {
  local names=()
  case "$SCOPE" in
    infra) names=(agent-ia-postgres agent-ia-redis) ;;
    omniroute) names=(omniroute) ;;
    all) names=(agent-ia-postgres agent-ia-redis omniroute) ;;
    *) fail "escopo inválido: $SCOPE" ;;
  esac
  local name state health
  for name in "${names[@]}"; do
    if ! container_exists "$name"; then
      printf '%-22s %s\n' "$name" "ausente"
      continue
    fi
    state="$("${DOCKER[@]}" inspect --format '{{.State.Status}}' "$name")"
    health="$("${DOCKER[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-healthcheck{{end}}' "$name")"
    printf '%-22s %-12s %s\n' "$name" "$state" "$health"
  done
}

case "$ACTION" in
  start) start_scope ;;
  stop) stop_scope ;;
  restart) stop_scope; start_scope ;;
  status) status_scope ;;
  *) fail "ação inválida: $ACTION; use start, stop, restart ou status" ;;
esac
