#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# Agent IA Interface - bootstrap completo para WSL/Ubuntu
# Não altera nem versiona o repositório remoto.
#
# Uso mais simples:
#   1) git clone https://github.com/gamajose/projeto-agent-ia-interface.git
#   2) coloque o .env compartilhado na raiz do clone
#   3) bash instalar_agent_ia_wsl.sh
#
# Também pode rodar fora do clone:
#   bash instalar_agent_ia_wsl.sh --env /caminho/para/.env
# ============================================================

REPO_URL="${AGENT_REPO_URL:-https://github.com/gamajose/projeto-agent-ia-interface.git}"
REPO_REF="${AGENT_REPO_REF:-main}"
DEFAULT_PROJECT_DIR="${AGENT_CLONE_DIR:-$HOME/projeto-agent-ia-interface}"
INSTALL_ROOT="${AGENT_INSTALL_ROOT:-/opt/agent-ia}"
OLLAMA_MODEL="${AGENT_OLLAMA_MODEL:-auto}"
ENV_SOURCE=""
PROJECT_DIR=""
VPN_USER=""
SKIP_CODEX=false
SKIP_OPENCODE=false
SKIP_OLLAMA=false

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Instalador completo do Agent IA Interface para WSL/Ubuntu.

Uso:
  bash instalar_agent_ia_wsl.sh [opções]

Opções:
  --env ARQUIVO           .env compartilhado que será usado na instalação
  --project-dir CAMINHO   clone local do projeto
  --install-root CAMINHO  instalação permanente (padrão: /opt/agent-ia)
  --vpn-user USUARIO      altera somente SSH_SRV_VPN_USER no .env
  --ollama-model MODELO   modelo do Ollama; padrão: auto
  --skip-codex            não instala o OpenAI Codex CLI
  --skip-opencode         não instala/configura OpenCode
  --skip-ollama           não instala Ollama/modelo local
  --help                  mostra esta ajuda
EOF
}

while (($#)); do
  case "$1" in
    --env)
      shift; [[ $# -gt 0 ]] || fail "informe o arquivo após --env"
      ENV_SOURCE="$1"
      ;;
    --project-dir)
      shift; [[ $# -gt 0 ]] || fail "informe o caminho após --project-dir"
      PROJECT_DIR="$1"
      ;;
    --install-root)
      shift; [[ $# -gt 0 ]] || fail "informe o caminho após --install-root"
      INSTALL_ROOT="$1"
      ;;
    --vpn-user)
      shift; [[ $# -gt 0 ]] || fail "informe o usuário após --vpn-user"
      VPN_USER="$1"
      ;;
    --ollama-model)
      shift; [[ $# -gt 0 ]] || fail "informe o modelo após --ollama-model"
      OLLAMA_MODEL="$1"
      ;;
    --skip-codex) SKIP_CODEX=true ;;
    --skip-opencode) SKIP_OPENCODE=true ;;
    --skip-ollama) SKIP_OLLAMA=true ;;
    --help|-h) usage; exit 0 ;;
    *) fail "opção desconhecida: $1" ;;
  esac
  shift
done

[[ "$INSTALL_ROOT" == /* ]] || fail "--install-root precisa ser absoluto"

TARGET_USER="${SUDO_USER:-${USER:-$(id -un)}}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" ]] || TARGET_HOME="$HOME"
TARGET_GROUP="$(id -gn "$TARGET_USER")"

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || fail "sudo não está instalado"
  sudo -v
  SUDO=(sudo)
fi

is_wsl() {
  grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null
}

systemd_active() {
  command -v systemctl >/dev/null 2>&1 \
    && [[ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" == "systemd" ]]
}

prepare_wsl_systemd() {
  if ! is_wsl; then
    return
  fi

  if systemd_active; then
    ok "WSL com systemd ativo"
    return
  fi

  warn "WSL detectado sem systemd ativo."
  "${SUDO[@]}" touch /etc/wsl.conf
  if grep -Eq '^[[:space:]]*systemd[[:space:]]*=' /etc/wsl.conf; then
    "${SUDO[@]}" sed -i -E 's/^[[:space:]]*systemd[[:space:]]*=.*/systemd=true/' /etc/wsl.conf
  elif grep -Eq '^[[:space:]]*\[boot\][[:space:]]*$' /etc/wsl.conf; then
    "${SUDO[@]}" sed -i '/^[[:space:]]*\[boot\][[:space:]]*$/a systemd=true' /etc/wsl.conf
  else
    printf '\n[boot]\nsystemd=true\n' | "${SUDO[@]}" tee -a /etc/wsl.conf >/dev/null
  fi

  cat <<'EOF'

O systemd foi habilitado em /etc/wsl.conf.
Essa alteração só entra em vigor após reiniciar o WSL.

No PowerShell do Windows execute:
  wsl --shutdown

Depois abra o WSL novamente e rode ESTE MESMO script.
EOF
  exit 20
}

install_base_packages() {
  command -v apt-get >/dev/null 2>&1 \
    || fail "este bootstrap foi preparado para WSL Ubuntu/Debian (apt-get)"

  info "Atualizando índices e instalando ferramentas de sistema"
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl git gnupg lsb-release \
    build-essential pkg-config \
    python3 python3-pip python3-venv python3-full \
    openssh-client sshpass \
    iproute2 iputils-ping traceroute dnsutils netcat-openbsd \
    nmap snmp jq unzip zip rsync procps
}

node_major() {
  if ! command -v node >/dev/null 2>&1; then
    printf '0'
    return
  fi
  node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || printf '0'
}

install_node() {
  local major
  major="$(node_major)"
  if [[ "$major" =~ ^[0-9]+$ ]] && ((major >= 20)); then
    info "Node.js já disponível: $(node --version)"
  else
    info "Instalando Node.js 22 para OpenCode/Codex"
    local setup
    setup="$(mktemp)"
    curl -fsSL https://deb.nodesource.com/setup_22.x -o "$setup"
    "${SUDO[@]}" -E bash "$setup"
    rm -f "$setup"
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  fi

  command -v node >/dev/null 2>&1 || fail "Node.js não foi instalado"
  command -v npm >/dev/null 2>&1 || fail "npm não foi instalado"

  major="$(node_major)"
  [[ "$major" =~ ^[0-9]+$ ]] && ((major >= 20)) \
    || fail "Node.js 20+ é necessário; encontrado $(node --version 2>/dev/null || true)"

  # Globais npm ficam no HOME do operador, sem sudo.
  mkdir -p "$TARGET_HOME/.local/bin"
  npm config set prefix "$TARGET_HOME/.local" --location=user >/dev/null
  export PATH="$TARGET_HOME/.local/bin:$PATH"
  hash -r

  if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$TARGET_HOME/.profile" 2>/dev/null; then
    printf '\n# Binários locais do Agent IA\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$TARGET_HOME/.profile"
  fi

  ok "Node $(node --version) / npm $(npm --version)"
}

install_codex() {
  $SKIP_CODEX && { warn "Codex ignorado por opção"; return; }

  info "Instalando/atualizando OpenAI Codex CLI"
  npm install -g @openai/codex@latest
  hash -r
  command -v codex >/dev/null 2>&1 || fail "Codex foi instalado, mas não apareceu no PATH"
  ok "Codex: $(codex --version 2>&1 | head -n 1)"
}

resolve_project_dir() {
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

  if [[ -n "$PROJECT_DIR" ]]; then
    PROJECT_DIR="$(readlink -f "$PROJECT_DIR")"
  elif [[ -f "$PWD/pyproject.toml" && -d "$PWD/app" && -d "$PWD/.git" ]]; then
    PROJECT_DIR="$PWD"
  elif [[ -f "$script_dir/pyproject.toml" && -d "$script_dir/app" && -d "$script_dir/.git" ]]; then
    PROJECT_DIR="$script_dir"
  else
    PROJECT_DIR="$DEFAULT_PROJECT_DIR"
  fi
}

prepare_repository() {
  resolve_project_dir

  if [[ -d "$PROJECT_DIR/.git" ]]; then
    info "Usando clone existente: $PROJECT_DIR"
    if git -C "$PROJECT_DIR" diff --quiet -- && git -C "$PROJECT_DIR" diff --cached --quiet --; then
      git -C "$PROJECT_DIR" fetch --prune origin
      git -C "$PROJECT_DIR" checkout "$REPO_REF"
      if git -C "$PROJECT_DIR" show-ref --verify --quiet "refs/remotes/origin/$REPO_REF"; then
        git -C "$PROJECT_DIR" merge --ff-only "origin/$REPO_REF"
      fi
    else
      warn "Há alterações locais no clone; não fiz pull para não sobrescrever nada."
    fi
  elif [[ -e "$PROJECT_DIR" ]]; then
    fail "$PROJECT_DIR existe, mas não é um clone Git válido"
  else
    info "Clonando projeto em $PROJECT_DIR"
    git clone --branch "$REPO_REF" --single-branch "$REPO_URL" "$PROJECT_DIR"
  fi

  [[ -f "$PROJECT_DIR/install.sh" ]] || fail "install.sh não encontrado no projeto"
}

prepare_env() {
  local target="$PROJECT_DIR/.env"

  if [[ -n "$ENV_SOURCE" ]]; then
    ENV_SOURCE="$(readlink -f "$ENV_SOURCE")"
    [[ -f "$ENV_SOURCE" ]] || fail ".env informado não existe: $ENV_SOURCE"
    info "Copiando .env compartilhado para o clone"
    cp "$ENV_SOURCE" "$target"
  fi

  [[ -f "$target" ]] \
    || fail "coloque o .env compartilhado em $PROJECT_DIR/.env ou use --env /caminho/.env"

  chmod 600 "$target"
  cp -a "$target" "$target.backup-$(date +%Y%m%d-%H%M%S)"

  local codex_path=""
  if command -v codex >/dev/null 2>&1; then
    codex_path="$(command -v codex)"
  fi

  # Ajusta SOMENTE chaves ligadas à máquina local.
  # APIs, senhas e tokens compartilhados são preservados.
  ENV_FILE="$target" \
  NEW_HOME="$TARGET_HOME" \
  NEW_USER="$TARGET_USER" \
  NEW_VPN_USER="$VPN_USER" \
  NEW_CODEX_PATH="$codex_path" \
  python3 - <<'PY'
from __future__ import annotations
import os
import re
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
home = os.environ["NEW_HOME"]
user = os.environ["NEW_USER"]
vpn_user = os.environ.get("NEW_VPN_USER", "")
codex_path = os.environ.get("NEW_CODEX_PATH", "")

lines = path.read_text(encoding="utf-8").splitlines()
positions: dict[str, int] = {}

for i, raw in enumerate(lines):
    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key = stripped.split("=", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        positions[key] = i

updates = {
    "AGENT_UI_OPERATOR_NAME": user,
    "SSH_KNOWN_HOSTS_PATH": f"{home}/.ssh/known_hosts",
    "CODEX_HOME": f"{home}/.codex",
    "OPENCODE_CONFIG_PATH": f"{home}/.config/opencode/opencode.json",
}
if codex_path:
    updates["CODEX_CLI_PATH"] = codex_path
if vpn_user:
    updates["SSH_SRV_VPN_USER"] = vpn_user

for key, value in updates.items():
    rendered = f"{key}={value}"
    if key in positions:
        lines[positions[key]] = rendered
    else:
        lines.append(rendered)

path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
path.chmod(0o600)
PY

  mkdir -p "$TARGET_HOME/.ssh"
  touch "$TARGET_HOME/.ssh/known_hosts"
  chmod 700 "$TARGET_HOME/.ssh"
  chmod 600 "$TARGET_HOME/.ssh/known_hosts"

  ok ".env preservado; apenas caminhos/identidade local foram ajustados"
}

run_project_installer() {
  local args=(
    --install-dir "$INSTALL_ROOT"
    --non-interactive
  )

  if $SKIP_OPENCODE; then
    args+=(--without-opencode)
  else
    args+=(--with-opencode)
  fi

  if $SKIP_OLLAMA; then
    args+=(--without-ollama)
  else
    args+=(--with-ollama --ollama-model "$OLLAMA_MODEL")
  fi

  info "Executando o instalador oficial do próprio projeto"
  (
    cd "$PROJECT_DIR"
    bash ./install.sh "${args[@]}"
  )
}

patch_installed_env() {
  local installed_env="$INSTALL_ROOT/app/.env"
  [[ -f "$installed_env" ]] || fail "não encontrei o .env instalado em $installed_env"

  local codex_path=""
  if command -v codex >/dev/null 2>&1; then
    codex_path="$(command -v codex)"
  fi

  ENV_FILE="$installed_env" \
  NEW_HOME="$TARGET_HOME" \
  NEW_USER="$TARGET_USER" \
  NEW_CODEX_PATH="$codex_path" \
  python3 - <<'PY'
from __future__ import annotations
import os
import re
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
home = os.environ["NEW_HOME"]
user = os.environ["NEW_USER"]
codex_path = os.environ.get("NEW_CODEX_PATH", "")

lines = path.read_text(encoding="utf-8").splitlines()
positions: dict[str, int] = {}
for i, raw in enumerate(lines):
    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key = stripped.split("=", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        positions[key] = i

updates = {
    "AGENT_UI_OPERATOR_NAME": user,
    "SSH_KNOWN_HOSTS_PATH": f"{home}/.ssh/known_hosts",
    "CODEX_HOME": f"{home}/.codex",
}
if codex_path:
    updates["CODEX_CLI_PATH"] = codex_path

for key, value in updates.items():
    rendered = f"{key}={value}"
    if key in positions:
        lines[positions[key]] = rendered
    else:
        lines.append(rendered)

path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
path.chmod(0o600)
PY

  "${SUDO[@]}" chown "$TARGET_USER:$TARGET_GROUP" "$installed_env"
  "${SUDO[@]}" chmod 600 "$installed_env"

  "${SUDO[@]}" systemctl restart agent-ia-worker.service agent-ia-web.service
  if ! $SKIP_OPENCODE && systemctl cat opencode-web.service >/dev/null 2>&1; then
    "${SUDO[@]}" systemctl restart opencode-web.service
  fi
}

service_state() {
  local unit="$1"
  if systemctl cat "$unit" >/dev/null 2>&1; then
    systemctl is-active "$unit" 2>/dev/null || true
  else
    printf 'não instalado'
  fi
}

validate_installation() {
  local installed_env="$INSTALL_ROOT/app/.env"
  local ui_port host_ip
  ui_port="$(awk -F= '$1=="AGENT_UI_PORT" {gsub(/["\r]/,"",$2); print $2; exit}' "$installed_env")"
  ui_port="${ui_port:-8080}"
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  host_ip="${host_ip:-127.0.0.1}"

  info "Validando serviços"
  for unit in agent-ia-infra.service omniroute.service agent-ia-worker.service agent-ia-web.service; do
    state="$(service_state "$unit")"
    [[ "$state" == "active" ]] || warn "$unit: $state"
  done

  if ! $SKIP_OPENCODE; then
    state="$(service_state opencode-web.service)"
    [[ "$state" == "active" ]] || warn "opencode-web.service: $state"
  fi

  "${SUDO[@]}" docker inspect agent-ia-postgres >/dev/null 2>&1 \
    && ok "PostgreSQL Docker criado"
  "${SUDO[@]}" docker inspect agent-ia-redis >/dev/null 2>&1 \
    && ok "Redis Docker criado"

  if curl -fsS --max-time 5 "http://127.0.0.1:${ui_port}/ui" >/dev/null 2>&1; then
    ok "Interface web respondendo"
  else
    warn "Interface ainda não respondeu em 127.0.0.1:${ui_port}"
  fi

  if ! $SKIP_OLLAMA && command -v ollama >/dev/null 2>&1; then
    ok "Ollama: $(ollama --version 2>&1 | head -n 1)"
    OLLAMA_HOST=http://127.0.0.1:11434 ollama list 2>/dev/null || true
  fi

  if ! $SKIP_CODEX && command -v codex >/dev/null 2>&1; then
    ok "Codex: $(codex --version 2>&1 | head -n 1)"
  fi

  if ! $SKIP_OPENCODE && command -v opencode >/dev/null 2>&1; then
    ok "OpenCode: $(opencode --version 2>&1 | head -n 1)"
  fi

  cat <<EOF

============================================================
 INSTALAÇÃO CONCLUÍDA
============================================================

Clone usado:
  $PROJECT_DIR

Instalação permanente:
  $INSTALL_ROOT

Interface:
  http://$host_ip:$ui_port/ui

Serviços principais:
  sudo systemctl status agent-ia-infra omniroute agent-ia-worker agent-ia-web --no-pager -l

Containers:
  sudo docker ps

Ollama:
  ollama list

Codex:
  codex --version

OpenCode:
  opencode --version

IMPORTANTE SOBRE O CODEX:
  A instalação do executável é automática, mas a autenticação é LOCAL.
  O script não copia a sessão ~/.codex de outra pessoa.

  Se o .env possuir uma OPENAI_API_KEY corporativa válida, a aplicação
  poderá exportá-la ao iniciar. Caso contrário, faça o login local uma vez:
    codex --login

IMPORTANTE SOBRE O OMNIROUTE:
  PostgreSQL, Redis e OmniRoute são criados localmente nesta máquina.
  O .env é preservado, mas o volume/banco interno de outra instalação do
  OmniRoute não é clonado pelo Git. Se o seu OMNIROUTE_API_KEY aponta para
  um endpoint criado apenas na outra máquina, será necessário configurar
  o OmniRoute local ou usar um gateway compartilhado.

Nenhuma senha/token do .env foi exibida por este instalador.
============================================================
EOF
}

main() {
  prepare_wsl_systemd
  install_base_packages
  install_node
  install_codex
  prepare_repository
  prepare_env
  run_project_installer
  patch_installed_env
  validate_installation
}

main "$@"
