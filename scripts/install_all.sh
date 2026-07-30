#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
TARGET_USER="${SUDO_USER:-${USER:-$(id -un)}}"
NON_INTERACTIVE=false
SKIP_DOCKER=false
OPENCODE_MODE="no"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Instalador completo do Agent IA.

Uso:
  bash scripts/install_all.sh [opções]

Opções:
  --install-root CAMINHO
  --user USUARIO
  --non-interactive
  --skip-docker
  --with-opencode
  --without-opencode
EOF
}

while (($#)); do
  case "$1" in
    --install-root) shift; [[ $# -gt 0 ]] || fail "informe --install-root"; INSTALL_ROOT="$1" ;;
    --user) shift; [[ $# -gt 0 ]] || fail "informe --user"; TARGET_USER="$1" ;;
    --non-interactive) NON_INTERACTIVE=true ;;
    --skip-docker) SKIP_DOCKER=true ;;
    --with-opencode) OPENCODE_MODE="yes" ;;
    --without-opencode) OPENCODE_MODE="no" ;;
    --help|-h) usage; exit 0 ;;
    *) fail "opção desconhecida: $1" ;;
  esac
  shift
done

[[ "$INSTALL_ROOT" == /* ]] || fail "a raiz da instalação precisa ser absoluta"
id "$TARGET_USER" >/dev/null 2>&1 || fail "usuário inexistente: $TARGET_USER"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$INSTALL_ROOT/venv"
CONFIG_DIR="$INSTALL_ROOT/config"
DATA_DIR="$INSTALL_ROOT/data"
ENV_FILE="$APP_DIR/.env"
OMNIROUTE_ENV="$CONFIG_DIR/omniroute.env"

[[ -f "$APP_DIR/pyproject.toml" ]] || fail "projeto não encontrado em $APP_DIR"

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || fail "sudo é necessário"
  sudo -v
  SUDO=(sudo)
fi

as_target() {
  if ((EUID == 0)) && [[ "$(id -un)" != "$TARGET_USER" ]]; then
    sudo -u "$TARGET_USER" -H "$@"
  else
    "$@"
  fi
}

systemd_available() {
  command -v systemctl >/dev/null 2>&1 && [[ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" == "systemd" ]]
}

if ! systemd_available; then
  if grep -qi microsoft /proc/version 2>/dev/null; then
    fail "WSL detectado sem systemd. Habilite [boot] systemd=true em /etc/wsl.conf, encerre o WSL manualmente e execute o instalador novamente. O script não reinicia a máquina."
  fi
  fail "esta instalação requer systemd para criar os serviços permanentes"
fi

install_system_packages() {
  info "Validando pacotes básicos do sistema"
  if command -v apt-get >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      ca-certificates curl git openssl python3 python3-pip python3-venv python3-full
  elif command -v dnf >/dev/null 2>&1; then
    "${SUDO[@]}" dnf install -y ca-certificates curl git openssl python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then
    "${SUDO[@]}" yum install -y ca-certificates curl git openssl python3 python3-pip
  elif command -v zypper >/dev/null 2>&1; then
    "${SUDO[@]}" zypper --non-interactive install ca-certificates curl git openssl python3 python3-pip
  else
    fail "gerenciador de pacotes não reconhecido"
  fi
}

install_compose_plugin() {
  docker compose version >/dev/null 2>&1 && return
  info "Instalando o plugin Docker Compose"
  if command -v apt-get >/dev/null 2>&1; then
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin 2>/dev/null \
      || "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
  elif command -v dnf >/dev/null 2>&1; then
    "${SUDO[@]}" dnf install -y docker-compose-plugin
  elif command -v yum >/dev/null 2>&1; then
    "${SUDO[@]}" yum install -y docker-compose-plugin
  else
    fail "Docker Compose não está disponível"
  fi
  docker compose version >/dev/null 2>&1 || "${SUDO[@]}" docker compose version >/dev/null 2>&1 \
    || fail "não foi possível instalar Docker Compose v2"
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    info "Docker já instalado; preservando a instalação existente"
  elif $SKIP_DOCKER; then
    fail "--skip-docker foi informado, mas o comando docker não existe"
  else
    info "Docker não encontrado; instalando pelo instalador oficial"
    local installer
    installer="$(mktemp)"
    curl -fsSL "${DOCKER_INSTALL_URL:-https://get.docker.com}" -o "$installer"
    "${SUDO[@]}" sh "$installer"
    rm -f "$installer"
  fi

  if ! docker info >/dev/null 2>&1 && ! "${SUDO[@]}" docker info >/dev/null 2>&1; then
    info "Ativando o serviço Docker"
    "${SUDO[@]}" systemctl enable --now docker.service
  fi
  "${SUDO[@]}" docker info >/dev/null 2>&1 || fail "o daemon Docker não respondeu"
  install_compose_plugin

  if getent group docker >/dev/null 2>&1 && ! id -nG "$TARGET_USER" | grep -qw docker; then
    "${SUDO[@]}" usermod -aG docker "$TARGET_USER"
    warn "o usuário $TARGET_USER foi incluído no grupo docker; novos terminais receberão essa permissão"
  fi
}

read_default() {
  local variable="$1" prompt="$2" default="$3" value=""
  if $NON_INTERACTIVE; then
    printf -v "$variable" '%s' "$default"
    return
  fi
  read -r -p "$prompt [$default]: " value
  printf -v "$variable" '%s' "${value:-$default}"
}

read_optional_secret() {
  local variable="$1" prompt="$2" value=""
  if $NON_INTERACTIVE; then
    printf -v "$variable" '%s' ""
    return
  fi
  read -r -s -p "$prompt: " value
  printf '\n'
  printf -v "$variable" '%s' "$value"
}

ask_yes_no() {
  local prompt="$1" default="${2:-no}" answer=""
  if $NON_INTERACTIVE; then
    [[ "$default" == "yes" ]]
    return
  fi
  if [[ "$default" == "yes" ]]; then
    read -r -p "$prompt [S/n]: " answer
    [[ -z "$answer" || "$answer" =~ ^[sSyY]$ ]]
  else
    read -r -p "$prompt [s/N]: " answer
    [[ "$answer" =~ ^[sSyY]$ ]]
  fi
}

detect_allowed_networks() {
  local values=("127.0.0.1/32" "::1/128") default_if route
  default_if="$(ip -4 route show default 2>/dev/null | awk 'NR==1 {print $5}')"
  if [[ -n "$default_if" ]]; then
    while IFS= read -r route; do
      [[ -n "$route" ]] && values+=("$route")
    done < <(ip -4 route show dev "$default_if" proto kernel scope link 2>/dev/null | awk '$1 ~ /^[0-9]+\./ && $1 ~ /\// {print $1}')
  fi
  local joined="" item
  for item in "${values[@]}"; do
    [[ ",$joined," == *",$item,"* ]] && continue
    joined="${joined:+$joined,}$item"
  done
  printf '%s' "$joined"
}

wait_container() {
  local name="$1" timeout="${2:-120}" elapsed=0 state health
  while ((elapsed < timeout)); do
    state="$("${SUDO[@]}" docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null || true)"
    health="$("${SUDO[@]}" docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || true)"
    if [[ "$state" == "running" && ("$health" == "healthy" || "$health" == "none") ]]; then
      ok "$name está pronto"
      return
    fi
    [[ "$state" == "exited" || "$state" == "dead" ]] && fail "$name encerrou durante a inicialização"
    sleep 3
    elapsed=$((elapsed + 3))
  done
  fail "$name não ficou pronto em ${timeout}s"
}

wait_omniroute() {
  local timeout="${1:-180}" elapsed=0 port code
  port="$(awk -F= '$1=="OMNIROUTE_PORT" {gsub(/["\r]/, "", $2); print $2; exit}' "$ENV_FILE")"
  port="${port:-20128}"
  while ((elapsed < timeout)); do
    code="$(curl -sS --max-time 4 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/" 2>/dev/null || true)"
    if [[ "$code" =~ ^[234][0-9][0-9]$ ]]; then
      ok "OmniRoute está pronto em 127.0.0.1:$port"
      return
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done
  fail "OmniRoute não respondeu em 127.0.0.1:$port após ${timeout}s"
}

install_system_packages
install_docker

"${SUDO[@]}" mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$VENV_DIR"
"${SUDO[@]}" chown -R "$TARGET_USER:$TARGET_GROUP" "$INSTALL_ROOT"
"${SUDO[@]}" chmod 700 "$CONFIG_DIR" "$DATA_DIR"

info "Preparando Python em $VENV_DIR"
as_target env AGENT_VENV_DIR="$VENV_DIR" bash "$APP_DIR/scripts/setup_wsl.sh"

OPERATOR_NAME="$TARGET_USER"
SSH_USER="2com"
SSH_PASSWORD=""
BASTION_HOST=""
BASTION_PORT="22"
BASTION_USER=""
BASTION_PASSWORD=""
OMNIROUTE_PASSWORD=""
ALLOWED_NETWORKS="$(detect_allowed_networks)"

read_default OPERATOR_NAME "Nome exibido do operador" "$OPERATOR_NAME"
read_default SSH_USER "Usuário SSH padrão dos alvos" "$SSH_USER"
read_optional_secret SSH_PASSWORD "Senha SSH padrão opcional (Enter mantém vazia/chave SSH)"

if ask_yes_no "Configurar agora o bastion/servidor de VPN" "no"; then
  read_default BASTION_HOST "IP ou hostname do bastion" ""
  read_default BASTION_PORT "Porta SSH do bastion" "22"
  read_default BASTION_USER "Usuário do bastion" "$TARGET_USER"
  read_optional_secret BASTION_PASSWORD "Senha do bastion opcional (Enter usa chave/agent)"
fi
read_optional_secret OMNIROUTE_PASSWORD "Senha inicial do painel OmniRoute (Enter gera uma senha forte)"

info "Gerando e preservando configurações em arquivos protegidos"
as_target env \
  INSTALL_SSH_PASSWORD="$SSH_PASSWORD" \
  INSTALL_BASTION_HOST="$BASTION_HOST" \
  INSTALL_BASTION_PORT="$BASTION_PORT" \
  INSTALL_BASTION_USER="$BASTION_USER" \
  INSTALL_BASTION_PASSWORD="$BASTION_PASSWORD" \
  INSTALL_OMNIROUTE_PASSWORD="$OMNIROUTE_PASSWORD" \
  "$VENV_DIR/bin/python" "$APP_DIR/scripts/configure_install_env.py" \
    --env "$ENV_FILE" \
    --example "$APP_DIR/.env.example" \
    --omniroute-env "$OMNIROUTE_ENV" \
    --install-root "$INSTALL_ROOT" \
    --app-dir "$APP_DIR" \
    --venv-dir "$VENV_DIR" \
    --operator "$OPERATOR_NAME" \
    --ssh-user "$SSH_USER" \
    --allowed-networks "$ALLOWED_NETWORKS" >/dev/null

"${SUDO[@]}" chown "$TARGET_USER:$TARGET_GROUP" "$ENV_FILE" "$OMNIROUTE_ENV"
"${SUDO[@]}" chmod 600 "$ENV_FILE" "$OMNIROUTE_ENV"
"${SUDO[@]}" chmod +x "$APP_DIR/install.sh" "$APP_DIR/scripts/"*.sh

info "Criando serviços systemd"
"${SUDO[@]}" tee /etc/systemd/system/agent-ia-infra.service >/dev/null <<EOF
[Unit]
Description=Agent IA - PostgreSQL e Redis
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=AGENT_INSTALL_ROOT=$INSTALL_ROOT
Environment=AGENT_APP_DIR=$APP_DIR
Environment=AGENT_ENV_FILE=$ENV_FILE
ExecStart=$APP_DIR/scripts/stack_control.sh start infra
ExecStop=$APP_DIR/scripts/stack_control.sh stop infra
TimeoutStartSec=180
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
EOF

"${SUDO[@]}" tee /etc/systemd/system/omniroute.service >/dev/null <<EOF
[Unit]
Description=OmniRoute local do Agent IA
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=AGENT_INSTALL_ROOT=$INSTALL_ROOT
Environment=AGENT_APP_DIR=$APP_DIR
Environment=AGENT_ENV_FILE=$ENV_FILE
ExecStart=$APP_DIR/scripts/stack_control.sh start omniroute
ExecStop=$APP_DIR/scripts/stack_control.sh stop omniroute
TimeoutStartSec=180
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
EOF

"${SUDO[@]}" tee /etc/systemd/system/agent-ia-web.service >/dev/null <<EOF
[Unit]
Description=Agent IA - Interface Web
After=network-online.target agent-ia-infra.service omniroute.service
Requires=agent-ia-infra.service
Wants=network-online.target omniroute.service

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$APP_DIR
Environment=HOME=$TARGET_HOME
Environment=AGENT_INSTALL_ROOT=$INSTALL_ROOT
Environment=AGENT_VENV_DIR=$VENV_DIR
Environment=AGENT_ENV_FILE=$ENV_FILE
ExecStart=$APP_DIR/scripts/start_web.sh
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

"${SUDO[@]}" tee /etc/systemd/system/agent-ia-worker.service >/dev/null <<EOF
[Unit]
Description=Agent IA - Worker operacional
After=network-online.target agent-ia-infra.service
Requires=agent-ia-infra.service
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$APP_DIR
Environment=HOME=$TARGET_HOME
Environment=AGENT_INSTALL_ROOT=$INSTALL_ROOT
Environment=AGENT_VENV_DIR=$VENV_DIR
Environment=AGENT_ENV_FILE=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/agent-worker run
Restart=always
RestartSec=4
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now agent-ia-infra.service omniroute.service
wait_container agent-ia-postgres 150
wait_container agent-ia-redis 90
wait_omniroute 180

info "Inicializando o schema do PostgreSQL"
as_target env AGENT_ENV_FILE="$ENV_FILE" AGENT_VENV_DIR="$VENV_DIR" \
  "$VENV_DIR/bin/python" -m app.db.init_db

"${SUDO[@]}" systemctl enable --now agent-ia-worker.service agent-ia-web.service

if [[ "$OPENCODE_MODE" == "yes" ]]; then
  info "Preparando o OpenCode integrado"
  as_target env AGENT_ENV_FILE="$ENV_FILE" AGENT_VENV="$VENV_DIR" bash "$APP_DIR/scripts/setup_opencode.sh" \
    || warn "o núcleo foi instalado, mas o OpenCode não pôde ser concluído; consulte a saída acima"
fi

sleep 2
"${SUDO[@]}" systemctl is-active --quiet agent-ia-worker.service || fail "agent-ia-worker não iniciou"
"${SUDO[@]}" systemctl is-active --quiet agent-ia-web.service || fail "agent-ia-web não iniciou"

UI_PORT="$(awk -F= '$1=="AGENT_UI_PORT" {gsub(/["\r]/, "", $2); print $2; exit}' "$ENV_FILE")"
UI_PORT="${UI_PORT:-8080}"
OMNIROUTE_PORT="$(awk -F= '$1=="OMNIROUTE_PORT" {gsub(/["\r]/, "", $2); print $2; exit}' "$ENV_FILE")"
OMNIROUTE_PORT="${OMNIROUTE_PORT:-20128}"
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-127.0.0.1}"

ok "Instalação concluída"
cat <<EOF

Caminho permanente:
  $INSTALL_ROOT

Interface:
  http://$HOST_IP:$UI_PORT/ui

OmniRoute local:
  http://127.0.0.1:$OMNIROUTE_PORT

Arquivos protegidos:
  $ENV_FILE
  $OMNIROUTE_ENV

Serviços:
  sudo systemctl status agent-ia-infra omniroute agent-ia-worker agent-ia-web --no-pager -l

Logs da aplicação:
  sudo journalctl -u agent-ia-web -f

Logs do worker:
  sudo journalctl -u agent-ia-worker -f

O instalador não grava a senha do sudo. Senhas SSH opcionais ficam somente no .env com modo 600.
Para consultar ou trocar a senha inicial do OmniRoute, edite $OMNIROUTE_ENV com acesso administrativo.
EOF
