#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
APP_DIR="${AGENT_APP_DIR:-$INSTALL_ROOT/app}"
ENV_FILE="${AGENT_ENV_FILE:-$APP_DIR/.env}"
COMPOSE_FILE="${AGENT_COMPOSE_FILE:-$APP_DIR/docker-compose.yml}"
DATA_DIR="${AGENT_DATA_DIR:-$INSTALL_ROOT/data}"
OMNIROUTE_MODE_FILE="$DATA_DIR/omniroute.mode"
ACTION="${1:-status}"
SCOPE="${2:-all}"

info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[ERRO] %s\n' "$*" >&2; exit 1; }

[[ -f "$COMPOSE_FILE" ]] || fail "compose não encontrado: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || fail ".env não encontrado: $ENV_FILE"

read_env_value() {
  local key="$1" default="${2:-}" value=""
  value="$(awk -F= -v wanted="$key" '
    $1 == wanted {
      sub(/^[^=]*=/, "")
      gsub(/\r/, "")
      print
      exit
    }
  ' "$ENV_FILE")"
  value="${value#\"}"
  value="${value%\"}"
  printf '%s' "${value:-$default}"
}

OMNIROUTE_PORT="$(read_env_value OMNIROUTE_PORT 20128)"
[[ "$OMNIROUTE_PORT" =~ ^[0-9]+$ ]] || fail "OMNIROUTE_PORT inválida: $OMNIROUTE_PORT"

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

container_publishes_omniroute() {
  local published=""
  container_exists omniroute || return 1
  published="$("${DOCKER[@]}" inspect --format '{{with index .NetworkSettings.Ports "20128/tcp"}}{{range .}}{{.HostPort}}{{end}}{{end}}' omniroute 2>/dev/null || true)"
  [[ "$published" == "$OMNIROUTE_PORT" ]]
}

port_listener() {
  command -v ss >/dev/null 2>&1 || return 0
  ss -H -lntp 2>/dev/null | awk -v suffix=":$OMNIROUTE_PORT" '$4 ~ suffix "$" {print; exit}'
}

omniroute_http_ready() {
  local code=""
  command -v curl >/dev/null 2>&1 || return 1
  code="$(curl -sS --max-time 4 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$OMNIROUTE_PORT/" 2>/dev/null || true)"
  [[ "$code" =~ ^[234][0-9][0-9]$ ]]
}

write_omniroute_mode() {
  mkdir -p "$DATA_DIR"
  printf '%s\n' "$1" > "$OMNIROUTE_MODE_FILE"
  chmod 600 "$OMNIROUTE_MODE_FILE" 2>/dev/null || true
}

read_omniroute_mode() {
  [[ -f "$OMNIROUTE_MODE_FILE" ]] && head -n 1 "$OMNIROUTE_MODE_FILE" || true
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

ensure_omniroute() {
  local listener=""
  listener="$(port_listener)"

  if [[ -n "$listener" ]]; then
    if container_running omniroute && container_publishes_omniroute; then
      write_omniroute_mode container
      info "Reutilizando container ativo: omniroute"
      return
    fi

    if omniroute_http_ready && grep -qi 'omniroute' <<<"$listener"; then
      write_omniroute_mode external
      info "Reutilizando OmniRoute externo já ativo em 127.0.0.1:$OMNIROUTE_PORT"
      if container_running omniroute; then
        info "Container omniroute paralelo preservado; a porta continua atendida pelo serviço externo"
      fi
      return
    fi

    fail "a porta $OMNIROUTE_PORT já está ocupada por outro processo: $listener"
  fi

  if container_running omniroute && ! container_publishes_omniroute; then
    fail "o container omniroute está ativo, mas sem publicar a porta $OMNIROUTE_PORT; revise o container antes de continuar"
  fi

  ensure_container omniroute omniroute
  write_omniroute_mode container
}

stop_container() {
  local name="$1"
  local timeout="${2:-30}"
  container_exists "$name" || return 0
  container_running "$name" || return 0
  info "Parando $name"
  "${DOCKER[@]}" stop -t "$timeout" "$name" >/dev/null
}

stop_omniroute() {
  local mode=""
  mode="$(read_omniroute_mode)"
  if [[ "$mode" == "external" ]]; then
    info "OmniRoute externo preservado; esta unidade não controla seu processo"
    return
  fi
  stop_container omniroute 40
}

start_scope() {
  case "$SCOPE" in
    infra)
      ensure_container postgres agent-ia-postgres
      ensure_container redis agent-ia-redis
      ;;
    omniroute)
      ensure_omniroute
      ;;
    all)
      ensure_container postgres agent-ia-postgres
      ensure_container redis agent-ia-redis
      ensure_omniroute
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
      stop_omniroute
      ;;
    all)
      stop_omniroute
      stop_container agent-ia-redis 20
      stop_container agent-ia-postgres 30
      ;;
    *) fail "escopo inválido: $SCOPE" ;;
  esac
}

status_container() {
  local name="$1" state health
  if ! container_exists "$name"; then
    printf '%-22s %s\n' "$name" "ausente"
    return
  fi
  state="$("${DOCKER[@]}" inspect --format '{{.State.Status}}' "$name")"
  health="$("${DOCKER[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-healthcheck{{end}}' "$name")"
  printf '%-22s %-12s %s\n' "$name" "$state" "$health"
}

status_omniroute() {
  local listener="" suffix=""
  listener="$(port_listener)"
  if [[ -n "$listener" ]] && omniroute_http_ready && grep -qi 'omniroute' <<<"$listener"; then
    container_running omniroute && suffix="; container paralelo preservado"
    printf '%-22s %-12s %s\n' "omniroute" "externo" "saudável na porta $OMNIROUTE_PORT$suffix"
    return
  fi
  status_container omniroute
}

status_scope() {
  case "$SCOPE" in
    infra)
      status_container agent-ia-postgres
      status_container agent-ia-redis
      ;;
    omniroute)
      status_omniroute
      ;;
    all)
      status_container agent-ia-postgres
      status_container agent-ia-redis
      status_omniroute
      ;;
    *) fail "escopo inválido: $SCOPE" ;;
  esac
}

case "$ACTION" in
  start) start_scope ;;
  stop) stop_scope ;;
  restart) stop_scope; start_scope ;;
  status) status_scope ;;
  *) fail "ação inválida: $ACTION; use start, stop, restart ou status" ;;
esac
