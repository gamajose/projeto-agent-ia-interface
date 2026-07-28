#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
VENV_DIR="${AGENT_VENV_DIR:-$HOME/.venvs/$PROJECT_NAME}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RECREATE=false
INSTALL_SYSTEM_PACKAGES=true

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Prepara o Agent IA Interface no Linux/WSL.

Uso:
  bash scripts/setup_wsl.sh [opções]

Opções:
  --recreate             recria o ambiente virtual
  --no-system-packages   não tenta instalar python3-full/python3-venv
  --help                 exibe esta ajuda

Variáveis opcionais:
  AGENT_VENV_DIR          diretório do ambiente virtual
                          padrão: $HOME/.venvs/$PROJECT_NAME
  PYTHON_BIN              executável Python; padrão: python3
EOF
}

while (($#)); do
    case "$1" in
        --recreate) RECREATE=true ;;
        --no-system-packages) INSTALL_SYSTEM_PACKAGES=false ;;
        --help|-h) usage; exit 0 ;;
        *) fail "opção desconhecida: $1" ;;
    esac
    shift
done

install_python_packages() {
    if ! command -v apt-get >/dev/null 2>&1; then
        fail "não foi possível criar o venv. Instale manualmente o pacote de venv do Python da sua distribuição."
    fi
    if ! $INSTALL_SYSTEM_PACKAGES; then
        fail "o suporte a venv não está disponível e --no-system-packages foi informado."
    fi

    local sudo_cmd=()
    if ((EUID != 0)); then
        command -v sudo >/dev/null 2>&1 || fail "sudo não está instalado. Execute como root ou instale python3-full e python3-venv manualmente."
        sudo_cmd=(sudo)
    fi

    info "Instalando suporte do Python para ambientes virtuais..."
    "${sudo_cmd[@]}" apt-get update
    "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-full python3-venv
}

create_venv() {
    mkdir -p "$(dirname "$VENV_DIR")"

    if $RECREATE && [[ -e "$VENV_DIR" ]]; then
        info "Removendo ambiente virtual anterior: $VENV_DIR"
        rm -rf -- "$VENV_DIR"
    fi

    if [[ -x "$VENV_DIR/bin/python" && -f "$VENV_DIR/pyvenv.cfg" ]]; then
        info "Reutilizando ambiente virtual: $VENV_DIR"
        return
    fi

    [[ ! -e "$VENV_DIR" ]] || rm -rf -- "$VENV_DIR"
    info "Criando ambiente virtual fora de /mnt: $VENV_DIR"

    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        warn "A primeira tentativa de criar o ambiente virtual falhou."
        install_python_packages
        rm -rf -- "$VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "não foi possível criar o ambiente virtual em $VENV_DIR"
    fi
}

ensure_env_file() {
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        info "Arquivo .env existente preservado."
        return
    fi

    if [[ -f "$PROJECT_DIR/.env.example" ]]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        chmod 600 "$PROJECT_DIR/.env" 2>/dev/null || true
        warn "Foi criado .env a partir de .env.example. Revise POSTGRES_DSN, Redis, SSH e provedores de IA."
    else
        warn "O projeto não possui .env.example; crie o arquivo .env antes de iniciar."
    fi
}

append_env_default() {
    local key="$1"
    local value="$2"
    local env_file="$PROJECT_DIR/.env"
    [[ -f "$env_file" ]] || return
    grep -Eq "^[[:space:]]*${key}=" "$env_file" || printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || install_python_packages

if [[ "$PROJECT_DIR" == /mnt/* ]]; then
    info "Projeto detectado em $PROJECT_DIR. O venv ficará no filesystem Linux para evitar o erro lib -> lib64 do WSL."
fi

if [[ -d "$PROJECT_DIR/.venv" && ! -f "$PROJECT_DIR/.venv/pyvenv.cfg" ]]; then
    warn "Removendo o .venv incompleto criado dentro do projeto."
    rm -rf -- "$PROJECT_DIR/.venv"
elif [[ -f "$PROJECT_DIR/.venv/pyvenv.cfg" ]]; then
    warn "Existe um .venv no projeto, mas este instalador usará $VENV_DIR."
fi

create_venv

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

info "Atualizando pip, setuptools e wheel..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel

info "Instalando dependências do projeto..."
"$PIP" install -r "$PROJECT_DIR/requirements.txt"
"$PIP" install -e "$PROJECT_DIR"
"$PIP" check

ensure_env_file
append_env_default AGENT_UI_OPERATOR_NAME "${USER:-operador}"
append_env_default AGENT_UI_HOST "0.0.0.0"
append_env_default AGENT_UI_PORT "8080"
append_env_default AGENT_UI_ENABLED "true"
append_env_default AGENT_UI_ALLOWED_NETWORKS "127.0.0.1/32,::1/128"

info "Validando instalação..."
"$PYTHON" -m compileall -q "$PROJECT_DIR/app"
"$VENV_DIR/bin/agent" --version

cat <<EOF

Ambiente preparado com sucesso.

Ambiente virtual:
  $VENV_DIR

Ativar no terminal:
  source "$VENV_DIR/bin/activate"

Iniciar a interface sem ativar o venv:
  bash "$PROJECT_DIR/scripts/start_web.sh"

Abrir no navegador:
  http://localhost:8080/ui

Antes do primeiro uso, revise:
  $PROJECT_DIR/.env
EOF
