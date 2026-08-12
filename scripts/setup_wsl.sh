#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
VENV_DIR="${AGENT_VENV_DIR:-$HOME/.venvs/$PROJECT_NAME}"
RUNTIME_DIR="${AGENT_RUNTIME_DIR:-$(dirname "$VENV_DIR")/runtime}"
PYTHON_BIN="${PYTHON_BIN:-}"
REQUIRED_PYTHON="3.11"
UV_INSTALL_URL="${UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
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
  --no-system-packages   não tenta instalar pacotes pelo apt/dnf/yum/zypper
  --help                 exibe esta ajuda

Variáveis opcionais:
  AGENT_VENV_DIR          diretório do ambiente virtual
                          padrão: $HOME/.venvs/$PROJECT_NAME
  AGENT_RUNTIME_DIR       diretório para runtime Python gerenciado
                          padrão: $(dirname "$VENV_DIR")/runtime
  PYTHON_BIN              executável Python 3.11 exato
  UV_INSTALL_URL          instalador do uv usado somente como fallback

O runtime da aplicação é fixado em Python 3.11. Python 3.12, 3.13, 3.14 ou
outra versão instalada no sistema não será usada para criar o venv do Agent IA.
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
    [[ -n "$candidate" ]] || return 1
    if [[ "$candidate" == */* ]]; then
        [[ -x "$candidate" ]] || return 1
    else
        command -v "$candidate" >/dev/null 2>&1 || return 1
    fi
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1
}

select_python() {
    local candidate
    if [[ -n "$PYTHON_BIN" ]]; then
        python_supported "$PYTHON_BIN" || return 1
        command -v "$PYTHON_BIN" 2>/dev/null || printf '%s' "$PYTHON_BIN"
        return
    fi
    for candidate in python3.11 python3; do
        if python_supported "$candidate"; then
            command -v "$candidate"
            return
        fi
    done
    return 1
}

sudo_cmd() {
    if ((EUID == 0)); then
        return
    fi
    command -v sudo >/dev/null 2>&1 || fail "sudo não está instalado. Execute como root ou permita o runtime Python gerenciado."
    printf '%s\0' sudo
}

install_package_manager_python() {
    $INSTALL_SYSTEM_PACKAGES || return 1

    local sudo_prefix=()
    if ((EUID != 0)); then
        command -v sudo >/dev/null 2>&1 || return 1
        sudo_prefix=(sudo)
    fi

    info "Tentando instalar Python 3.11 pelo gerenciador de pacotes do sistema..."
    if command -v dnf >/dev/null 2>&1; then
        "${sudo_prefix[@]}" dnf install -y python3.11 python3.11-pip >/dev/null 2>&1 || return 1
    elif command -v yum >/dev/null 2>&1; then
        "${sudo_prefix[@]}" yum install -y python3.11 python3.11-pip >/dev/null 2>&1 || return 1
    elif command -v apt-get >/dev/null 2>&1; then
        "${sudo_prefix[@]}" apt-get update >/dev/null 2>&1 || return 1
        "${sudo_prefix[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            python3.11 python3.11-venv python3.11-dev >/dev/null 2>&1 || return 1
    elif command -v zypper >/dev/null 2>&1; then
        "${sudo_prefix[@]}" zypper --non-interactive install python311 python311-pip >/dev/null 2>&1 || return 1
    else
        return 1
    fi

    select_python >/dev/null 2>&1
}

ensure_curl() {
    command -v curl >/dev/null 2>&1 && return
    $INSTALL_SYSTEM_PACKAGES || fail "curl não está disponível e --no-system-packages foi informado"

    local sudo_prefix=()
    if ((EUID != 0)); then
        command -v sudo >/dev/null 2>&1 || fail "curl é necessário para instalar o Python 3.11 gerenciado"
        sudo_prefix=(sudo)
    fi

    if command -v apt-get >/dev/null 2>&1; then
        "${sudo_prefix[@]}" apt-get update
        "${sudo_prefix[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
        "${sudo_prefix[@]}" dnf install -y curl ca-certificates
    elif command -v yum >/dev/null 2>&1; then
        "${sudo_prefix[@]}" yum install -y curl ca-certificates
    elif command -v zypper >/dev/null 2>&1; then
        "${sudo_prefix[@]}" zypper --non-interactive install curl ca-certificates
    else
        fail "curl não está disponível para instalar o Python 3.11 gerenciado"
    fi
}

install_managed_python() {
    ensure_curl

    local uv_dir="$RUNTIME_DIR/uv"
    local python_dir="$RUNTIME_DIR/python"
    local uv_bin="$uv_dir/uv"
    local installer candidate

    mkdir -p "$uv_dir" "$python_dir"

    if [[ ! -x "$uv_bin" ]]; then
        info "Python 3.11 não está no repositório do sistema; instalando runtime gerenciado localmente..."
        installer="$(mktemp)"
        curl -fsSL "$UV_INSTALL_URL" -o "$installer" \
            || fail "não foi possível baixar o instalador do runtime Python"
        env UV_UNMANAGED_INSTALL="$uv_dir" UV_NO_MODIFY_PATH=1 sh "$installer" >/dev/null \
            || { rm -f "$installer"; fail "não foi possível instalar o gerenciador de runtime Python"; }
        rm -f "$installer"
    fi

    [[ -x "$uv_bin" ]] || fail "uv não foi instalado em $uv_dir"
    info "Instalando CPython $REQUIRED_PYTHON em $python_dir..."
    env UV_PYTHON_INSTALL_DIR="$python_dir" "$uv_bin" python install "$REQUIRED_PYTHON" >/dev/null \
        || fail "não foi possível instalar CPython $REQUIRED_PYTHON"

    candidate="$(env UV_PYTHON_INSTALL_DIR="$python_dir" "$uv_bin" python find "$REQUIRED_PYTHON" 2>/dev/null || true)"
    python_supported "$candidate" || fail "o runtime gerenciado não retornou Python $REQUIRED_PYTHON"
    PYTHON_BIN="$candidate"
}

ensure_supported_python() {
    local selected=""

    if [[ -n "$PYTHON_BIN" ]] && ! python_supported "$PYTHON_BIN"; then
        warn "Ignorando $PYTHON_BIN: o Agent IA usa Python $REQUIRED_PYTHON no runtime de produção."
        PYTHON_BIN=""
    fi

    if selected="$(select_python)"; then
        PYTHON_BIN="$selected"
        return
    fi

    if install_package_manager_python && selected="$(select_python)"; then
        PYTHON_BIN="$selected"
        return
    fi

    install_managed_python
    selected="$(select_python)" || fail "não foi possível localizar Python $REQUIRED_PYTHON após a instalação"
    PYTHON_BIN="$selected"
}

venv_supported() {
    [[ -x "$VENV_DIR/bin/python" && -f "$VENV_DIR/pyvenv.cfg" ]] || return 1
    "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1
}

create_venv() {
    mkdir -p "$(dirname "$VENV_DIR")"

    if $RECREATE && [[ -e "$VENV_DIR" ]]; then
        info "Removendo ambiente virtual anterior: $VENV_DIR"
        rm -rf -- "$VENV_DIR"
    elif [[ -e "$VENV_DIR" ]] && ! venv_supported; then
        warn "O ambiente virtual existente não usa Python $REQUIRED_PYTHON; recriando com $($PYTHON_BIN --version 2>&1)."
        rm -rf -- "$VENV_DIR"
    fi

    if venv_supported; then
        info "Reutilizando ambiente virtual Python $REQUIRED_PYTHON: $VENV_DIR"
        return
    fi

    [[ ! -e "$VENV_DIR" ]] || rm -rf -- "$VENV_DIR"
    info "Criando ambiente virtual com $($PYTHON_BIN --version 2>&1): $VENV_DIR"

    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        warn "A criação do venv falhou com o interpretador selecionado; usando runtime gerenciado Python $REQUIRED_PYTHON."
        install_managed_python
        rm -rf -- "$VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "não foi possível criar o ambiente virtual em $VENV_DIR"
    fi

    venv_supported || fail "o venv foi criado, mas não está usando Python $REQUIRED_PYTHON"
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
info "Interpretador selecionado para produção: $($PYTHON_BIN --version 2>&1) ($PYTHON_BIN)"

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
export PATH="$VENV_DIR/bin:$PATH"

info "Atualizando pip, setuptools e wheel no venv Python $REQUIRED_PYTHON..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel

info "Instalando dependências do projeto somente dentro do venv..."
"$PIP" install -r "$PROJECT_DIR/requirements.txt"
"$PIP" install -e "$PROJECT_DIR"
"$PIP" check

ensure_env_file
append_env_default AGENT_UI_OPERATOR_NAME "${USER:-operador}"
append_env_default AGENT_UI_HOST "0.0.0.0"
append_env_default AGENT_UI_PORT "8080"
append_env_default AGENT_UI_ENABLED "true"
append_env_default AGENT_UI_ALLOWED_NETWORKS "127.0.0.1/32,::1/128"
append_env_default AGENT_PYTHON_BIN "$PYTHON"

info "Validando instalação..."
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'
"$PYTHON" -m compileall -q "$PROJECT_DIR/app"
"$VENV_DIR/bin/agent" --version

cat <<EOF

Ambiente preparado com sucesso.

Python de produção:
  $($PYTHON --version 2>&1)
  $PYTHON

Ambiente virtual:
  $VENV_DIR

PATH usado pela sessão de instalação:
  $VENV_DIR/bin

Ativar no terminal:
  source "$VENV_DIR/bin/activate"

Iniciar a interface sem ativar o venv:
  bash "$PROJECT_DIR/scripts/start_web.sh"

Abrir no navegador:
  http://localhost:8080/ui

Antes do primeiro uso, revise:
  $PROJECT_DIR/.env
EOF