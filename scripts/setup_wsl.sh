#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
VENV_DIR="${AGENT_VENV_DIR:-$HOME/.venvs/$PROJECT_NAME}"
PYTHON_BIN="${PYTHON_BIN:-}"
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
  --no-system-packages   não tenta instalar pacotes Python do sistema
  --help                 exibe esta ajuda

Variáveis opcionais:
  AGENT_VENV_DIR          diretório do ambiente virtual
                          padrão: $HOME/.venvs/$PROJECT_NAME
  PYTHON_BIN              executável Python 3.11 ou superior
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

python_supported() {
    local candidate="$1"
    command -v "$candidate" >/dev/null 2>&1 || return 1
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

select_python() {
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

sudo_prefix() {
    if ((EUID == 0)); then
        return
    fi
    command -v sudo >/dev/null 2>&1 || fail "sudo não está instalado. Execute como root ou instale Python 3.11 manualmente."
    printf '%s\0' sudo
}

install_python_packages() {
    $INSTALL_SYSTEM_PACKAGES || fail "Python 3.11 não está disponível e --no-system-packages foi informado."

    local sudo_cmd=()
    if ((EUID != 0)); then
        sudo_cmd=(sudo)
    fi

    info "Instalando Python 3.11 e suporte a ambientes virtuais..."
    if command -v dnf >/dev/null 2>&1; then
        "${sudo_cmd[@]}" dnf install -y python3.11 python3.11-pip \
            || "${sudo_cmd[@]}" dnf install -y python3.12 python3.12-pip
    elif command -v yum >/dev/null 2>&1; then
        "${sudo_cmd[@]}" yum install -y python3.11 python3.11-pip \
            || "${sudo_cmd[@]}" yum install -y python3.12 python3.12-pip
    elif command -v apt-get >/dev/null 2>&1; then
        "${sudo_cmd[@]}" apt-get update
        "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            python3 python3-pip python3-venv python3-full
        if ! select_python >/dev/null 2>&1; then
            "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
                python3.11 python3.11-venv python3.11-dev
        fi
    elif command -v zypper >/dev/null 2>&1; then
        "${sudo_cmd[@]}" zypper --non-interactive install python311 python311-pip
    else
        fail "gerenciador de pacotes não reconhecido. Instale Python 3.11 ou superior manualmente."
    fi
}

ensure_supported_python() {
    local selected=""
    if selected="$(select_python)"; then
        PYTHON_BIN="$selected"
        return
    fi

    [[ -z "$PYTHON_BIN" ]] || fail "$PYTHON_BIN não atende ao requisito mínimo Python 3.11"
    install_python_packages
    selected="$(select_python)" || fail "não foi possível localizar Python 3.11 ou superior após a instalação"
    PYTHON_BIN="$selected"
}

venv_supported() {
    [[ -x "$VENV_DIR/bin/python" && -f "$VENV_DIR/pyvenv.cfg" ]] || return 1
    "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

create_venv() {
    mkdir -p "$(dirname "$VENV_DIR")"

    if $RECREATE && [[ -e "$VENV_DIR" ]]; then
        info "Removendo ambiente virtual anterior: $VENV_DIR"
        rm -rf -- "$VENV_DIR"
    elif [[ -e "$VENV_DIR" ]] && ! venv_supported; then
        warn "O ambiente virtual existente usa Python incompatível; recriando com $($PYTHON_BIN --version 2>&1)."
        rm -rf -- "$VENV_DIR"
    fi

    if venv_supported; then
        info "Reutilizando ambiente virtual: $VENV_DIR"
        return
    fi

    [[ ! -e "$VENV_DIR" ]] || rm -rf -- "$VENV_DIR"
    info "Criando ambiente virtual com $($PYTHON_BIN --version 2>&1): $VENV_DIR"

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

ensure_supported_python
info "Interpretador selecionado: $($PYTHON_BIN --version 2>&1)"

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

Python:
  $($PYTHON --version 2>&1)

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
