#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${AGENT_REPO_URL:-https://github.com/gamajose/projeto-agent-ia-interface.git}"
REPO_REF="${AGENT_REPO_REF:-main}"
INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
APP_DIR=""
NON_INTERACTIVE=false
SKIP_DOCKER=false
OPENCODE_MODE="ask"
PYTHON_BIN="${PYTHON_BIN:-}"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Instala o Agent IA em um caminho previsível no Linux ou WSL com systemd.

Uso local:
  bash install.sh [opções]

Uso em uma máquina nova:
  curl -fsSL https://raw.githubusercontent.com/gamajose/projeto-agent-ia-interface/main/install.sh | bash

Opções:
  --install-dir CAMINHO   raiz da instalação; padrão: /opt/agent-ia
  --ref REFERENCIA        branch ou tag; padrão: main
  --repo URL              repositório Git
  --non-interactive       não solicita dados opcionais
  --skip-docker           não instala Docker; exige Docker já funcional
  --with-opencode         prepara também o OpenCode integrado
  --without-opencode      não prepara o OpenCode
  --help                  mostra esta ajuda

Variáveis equivalentes:
  AGENT_INSTALL_ROOT, AGENT_REPO_URL, AGENT_REPO_REF e PYTHON_BIN
EOF
}

while (($#)); do
  case "$1" in
    --install-dir) shift; [[ $# -gt 0 ]] || fail "informe o caminho após --install-dir"; INSTALL_ROOT="$1" ;;
    --repo) shift; [[ $# -gt 0 ]] || fail "informe a URL após --repo"; REPO_URL="$1" ;;
    --ref) shift; [[ $# -gt 0 ]] || fail "informe a referência após --ref"; REPO_REF="$1" ;;
    --non-interactive) NON_INTERACTIVE=true ;;
    --skip-docker) SKIP_DOCKER=true ;;
    --with-opencode) OPENCODE_MODE="yes" ;;
    --without-opencode) OPENCODE_MODE="no" ;;
    --help|-h) usage; exit 0 ;;
    *) fail "opção desconhecida: $1" ;;
  esac
  shift
done

[[ "$INSTALL_ROOT" == /* ]] || fail "--install-dir precisa ser um caminho absoluto"
[[ "$INSTALL_ROOT" != *[[:space:]]* ]] || fail "--install-dir não pode conter espaços; use um caminho como /opt/agent-ia"
APP_DIR="$INSTALL_ROOT/app"
TARGET_USER="${SUDO_USER:-${USER:-$(id -un)}}"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$PWD}")" 2>/dev/null && pwd || printf '%s' "$PWD")"
SOURCE_ENV=""

if [[ -f "$SOURCE_DIR/pyproject.toml" && -d "$SOURCE_DIR/app" ]]; then
  [[ -f "$SOURCE_DIR/.env" ]] && SOURCE_ENV="$SOURCE_DIR/.env"
else
  SOURCE_DIR=""
fi

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || fail "sudo é necessário para instalar em $INSTALL_ROOT"
  sudo -v
  SUDO=(sudo)
fi

as_target() {
  if ((EUID == 0)) && [[ "$(id -un)" != "$TARGET_USER" ]]; then
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$TARGET_USER" -- "$@"
    elif command -v sudo >/dev/null 2>&1; then
      sudo -u "$TARGET_USER" -H "$@"
    else
      fail "não foi possível executar como $TARGET_USER; instale util-linux/runuser ou sudo"
    fi
  else
    "$@"
  fi
}

python_supported() {
  local candidate="$1"
  command -v "$candidate" >/dev/null 2>&1 || return 1
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

select_supported_python() {
  local candidate
  if [[ -n "$PYTHON_BIN" ]]; then
    python_supported "$PYTHON_BIN" || return 1
    printf '%s' "$PYTHON_BIN"
    return
  fi
  for candidate in python3.12 python3.11 python3; do
    if python_supported "$candidate"; then
      printf '%s' "$candidate"
      return
    fi
  done
  return 1
}

install_bootstrap_packages() {
  local missing=()
  for command in git curl; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
  done
  ((${#missing[@]} == 0)) && return

  info "Instalando ferramentas básicas: ${missing[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    "${SUDO[@]}" dnf install -y git curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then
    "${SUDO[@]}" yum install -y git curl ca-certificates
  elif command -v zypper >/dev/null 2>&1; then
    "${SUDO[@]}" zypper --non-interactive install git curl ca-certificates
  else
    fail "gerenciador de pacotes não reconhecido; instale git e curl"
  fi
}

ensure_supported_python() {
  local selected=""
  if selected="$(select_supported_python)"; then
    PYTHON_BIN="$selected"
    export PYTHON_BIN
    info "Python compatível detectado: $($PYTHON_BIN --version 2>&1)"
    return
  fi

  [[ -z "${PYTHON_BIN:-}" ]] || fail "$PYTHON_BIN não atende ao requisito mínimo Python 3.11"
  info "Python 3.11 ou superior não encontrado; instalando uma versão paralela compatível"

  if command -v dnf >/dev/null 2>&1; then
    "${SUDO[@]}" dnf install -y python3.11 python3.11-pip \
      || "${SUDO[@]}" dnf install -y python3.12 python3.12-pip
  elif command -v yum >/dev/null 2>&1; then
    "${SUDO[@]}" yum install -y python3.11 python3.11-pip \
      || "${SUDO[@]}" yum install -y python3.12 python3.12-pip
  elif command -v apt-get >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-venv python3-full
    if ! selected="$(select_supported_python)"; then
      "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y python3.11 python3.11-venv python3.11-dev
    fi
  elif command -v zypper >/dev/null 2>&1; then
    "${SUDO[@]}" zypper --non-interactive install python311 python311-pip
  else
    fail "gerenciador de pacotes não reconhecido; instale Python 3.11 ou superior"
  fi

  selected="$(select_supported_python)" \
    || fail "não foi possível disponibilizar Python 3.11 ou superior nesta distribuição"
  PYTHON_BIN="$selected"
  export PYTHON_BIN
  info "Python selecionado: $($PYTHON_BIN --version 2>&1)"
}

install_bootstrap_packages
ensure_supported_python
"${SUDO[@]}" mkdir -p "$INSTALL_ROOT"
"${SUDO[@]}" chown "$TARGET_USER:$TARGET_GROUP" "$INSTALL_ROOT"

if [[ -d "$APP_DIR/.git" ]]; then
  info "Instalação existente encontrada em $APP_DIR"
  if as_target git -C "$APP_DIR" diff --quiet && as_target git -C "$APP_DIR" diff --cached --quiet; then
    as_target git -C "$APP_DIR" fetch --prune origin
    as_target git -C "$APP_DIR" checkout "$REPO_REF"
    if as_target git -C "$APP_DIR" show-ref --verify --quiet "refs/remotes/origin/$REPO_REF"; then
      as_target git -C "$APP_DIR" merge --ff-only "origin/$REPO_REF"
    fi
  else
    warn "há alterações locais em $APP_DIR; o código foi preservado sem atualizar"
  fi
elif [[ -e "$APP_DIR" ]]; then
  fail "$APP_DIR já existe, mas não é um clone Git válido"
else
  info "Baixando o projeto para $APP_DIR"
  as_target git clone --branch "$REPO_REF" --single-branch "$REPO_URL" "$APP_DIR"
fi

if [[ -n "$SOURCE_ENV" && ! -f "$APP_DIR/.env" ]]; then
  info "Migrando o .env da cópia local para a instalação padronizada"
  "${SUDO[@]}" install -o "$TARGET_USER" -g "$TARGET_GROUP" -m 600 "$SOURCE_ENV" "$APP_DIR/.env"
fi

args=(--install-root "$INSTALL_ROOT" --user "$TARGET_USER")
$NON_INTERACTIVE && args+=(--non-interactive)
$SKIP_DOCKER && args+=(--skip-docker)
[[ "$OPENCODE_MODE" == "yes" ]] && args+=(--with-opencode)
[[ "$OPENCODE_MODE" == "no" ]] && args+=(--without-opencode)

bash "$APP_DIR/scripts/install_all.sh" "${args[@]}"

# Uma instalação anterior pode manter o processo web ativo no caminho antigo.
# Reiniciar somente esta unidade faz o systemd carregar WorkingDirectory e
# ExecStart já gravados para a nova raiz, sem reiniciar host ou containers.
info "Ativando a interface a partir de $APP_DIR"
"${SUDO[@]}" systemctl restart agent-ia-web.service
"${SUDO[@]}" systemctl is-active --quiet agent-ia-web.service \
  || fail "agent-ia-web não permaneceu ativo após a migração"
