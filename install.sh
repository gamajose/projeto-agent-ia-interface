#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${AGENT_REPO_URL:-https://github.com/gamajose/projeto-agent-ia-interface.git}"
REPO_REF="${AGENT_REPO_REF:-main}"
INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
APP_DIR=""
NON_INTERACTIVE=false
SKIP_DOCKER=false
OPENCODE_MODE="ask"

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
  AGENT_INSTALL_ROOT, AGENT_REPO_URL e AGENT_REPO_REF
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
    sudo -u "$TARGET_USER" -H "$@"
  else
    "$@"
  fi
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

install_bootstrap_packages
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

exec bash "$APP_DIR/scripts/install_all.sh" "${args[@]}"
