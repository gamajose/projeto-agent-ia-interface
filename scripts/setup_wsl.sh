#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
VENV_DIR="${AGENT_VENV_DIR:-$HOME/.venvs/$PROJECT_NAME}"
PYTHON_BIN="${PYTHON_BIN:-}"
RECREATE=false
INSTALL_SYSTEM_PACKAGES=true
PYTHON_REQUIRED_MAJOR=3
PYTHON_REQUIRED_MINOR=11

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Prepara o Agent IA Interface no Linux/WSL com Python 3.11.x.

Uso:
  bash scripts/setup_wsl.sh [opções]

Opções:
  --recreate             recria o ambiente virtual
  --no-system-packages   não tenta instalar pacotes Python do sistema
  --help                 exibe esta ajuda

Variáveis opcionais:
  AGENT_VENV_DIR          diretório do ambiente virtual
                          padrão: $HOME/.venvs/$PROJECT_NAME
  PYTHON_BIN              executável Python 3.11.x. Outras versões são ignoradas
                          pelo instalador para proteger a compatibilidade do projeto.
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
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1
}

python_realpath() {
    local candidate="$1"
    "$candidate" -c 'import os,sys; print(os.path.realpath(sys.executable))' 2>/dev/null
}

select_python() {
    local candidate uv_candidate=""

    if [[ -n "$PYTHON_BIN" ]]; then
        if python_supported "$PYTHON_BIN"; then
            python_realpath "$PYTHON_BIN"
            return
        fi
        warn "PYTHON_BIN aponta para $($PYTHON_BIN --version 2>&1 || printf 'um Python incompatível'); o Agent IA exige Python 3.11.x. A seleção será corrigida automaticamente."
        PYTHON_BIN=""
    fi

    for candidate in python3.11 /usr/bin/python3.11 /usr/local/bin/python3.11; do
        if python_supported "$candidate"; then
            python_realpath "$candidate"
            return
        fi
    done

    if command -v uv >/dev/null 2>&1; then
        uv_candidate="$(uv python find 3.11 2>/dev/null || true)"
        if [[ -n "$uv_candidate" ]] && python_supported "$uv_candidate"; then
            python_realpath "$uv_candidate"
            return
        fi
    fi

    return 1
}

install_uv_python311() {
    local uv_bin="" selected=""
    command -v curl >/dev/null 2>&1 || fail "curl é necessário para instalar o runtime Python 3.11 gerenciado"

    info "Python 3.11 não está disponível no repositório da distribuição; instalando runtime 3.11 gerenciado com uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$HOME/.local/bin" sh
    export PATH="$HOME/.local/bin:$PATH"
    uv_bin="$(command -v uv || true)"
    [[ -n "$uv_bin" ]] || fail "uv foi instalado, mas não ficou disponível em $HOME/.local/bin"

    "$uv_bin" python install 3.11
    selected="$("$uv_bin" python find 3.11 2>/dev/null || true)"
    [[ -n "$selected" ]] && python_supported "$selected" \
        || fail "o runtime gerenciado não disponibilizou Python 3.11.x"
}

install_python_packages() {
    $INSTALL_SYSTEM_PACKAGES || fail "Python 3.11.x não está disponível e --no-system-packages foi informado."

    local sudo_cmd=() selected=""
    if ((EUID != 0)); then
        command -v sudo >/dev/null 2>&1 || fail "sudo não está instalado. Execute como root ou disponibilize Python 3.11.x."
        sudo_cmd=(sudo)
    fi

    info "Instalando Python 3.11 e suporte a ambientes virtuais..."
    if command -v dnf >/dev/null 2>&1; then
        "${sudo_cmd[@]}" dnf install -y python3.11 python3.11-pip python3.11-devel || true
    elif command -v yum >/dev/null 2>&1; then
        "${sudo_cmd[@]}" yum install -y python3.11 python3.11-pip python3.11-devel || true
    elif command -v apt-get >/dev/null 2>&1; then
        "${sudo_cmd[@]}" apt-get update
        "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            ca-certificates curl python3.11 python3.11-venv python3.11-dev python3-pip || true
    elif command -v zypper >/dev/null 2>&1; then
        "${sudo_cmd[@]}" zypper --non-interactive install python311 python311-pip python311-devel || true
    fi

    selected="$(select_python 2>/dev/null || true)"
    if [[ -z "$selected" ]]; then
        install_uv_python311
    fi
}

ensure_supported_python() {
    local selected=""
    selected="$(select_python 2>/dev/null || true)"
    if [[ -z "$selected" ]]; then
        install_python_packages
        selected="$(select_python 2>/dev/null || true)"
    fi
    [[ -n "$selected" ]] || fail "não foi possível disponibilizar Python 3.11.x"
    PYTHON_BIN="$selected"
    export PYTHON_BIN
    info "Interpretador fixado: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"
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
        local old_version="desconhecido"
        [[ -x "$VENV_DIR/bin/python" ]] && old_version="$($VENV_DIR/bin/python --version 2>&1 || true)"
        warn "O venv existente usa $old_version; recriando obrigatoriamente com Python 3.11.x."
        rm -rf -- "$VENV_DIR"
    fi

    if venv_supported; then
        info "Reutilizando ambiente virtual Python 3.11: $VENV_DIR"
        return
    fi

    [[ ! -e "$VENV_DIR" ]] || rm -rf -- "$VENV_DIR"
    info "Criando ambiente virtual com $($PYTHON_BIN --version 2>&1): $VENV_DIR"

    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        warn "O módulo venv do Python 3.11 não está funcional; tentando runtime gerenciado."
        install_uv_python311
        PYTHON_BIN="$(select_python)"
        export PYTHON_BIN
        rm -rf -- "$VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "não foi possível criar o ambiente virtual em $VENV_DIR"
    fi

    venv_supported || fail "o ambiente virtual foi criado, mas não está usando Python 3.11.x"
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
    local key="$1" value="$2" env_file="$PROJECT_DIR/.env"
    [[ -f "$env_file" ]] || return
    grep -Eq "^[[:space:]]*${key}=" "$env_file" || printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
}

ensure_shell_path() {
    local profile="$HOME/.profile" marker_begin="# >>> Agent IA Python 3.11 >>>" marker_end="# <<< Agent IA Python 3.11 <<<"
    local tmp
    tmp="$(mktemp)"
    [[ -f "$profile" ]] && awk -v begin="$marker_begin" -v end="$marker_end" '
        $0 == begin {skip=1; next}
        $0 == end {skip=0; next}
        !skip {print}
    ' "$profile" > "$tmp"
    cat >> "$tmp" <<EOF
$marker_begin
export AGENT_VENV_DIR="$VENV_DIR"
if [ -d "\$AGENT_VENV_DIR/bin" ]; then
  export PATH="\$AGENT_VENV_DIR/bin:\$PATH"
fi
$marker_end
EOF
    mv "$tmp" "$profile"
    chmod 600 "$profile" 2>/dev/null || true
    info "PATH persistente configurado em $profile para novos terminais"
}

ensure_supported_python

if [[ "$PROJECT_DIR" == /mnt/* ]]; then
    info "Projeto detectado em $PROJECT_DIR. O venv ficará no filesystem Linux para evitar problemas do WSL."
fi

if [[ -d "$PROJECT_DIR/.venv" && ! -f "$PROJECT_DIR/.venv/pyvenv.cfg" ]]; then
    warn "Removendo o .venv incompleto criado dentro do projeto."
    rm -rf -- "$PROJECT_DIR/.venv"
elif [[ -f "$PROJECT_DIR/.venv/pyvenv.cfg" ]]; then
    warn "Existe um .venv no projeto, mas este instalador usará $VENV_DIR."
fi

create_venv
ensure_shell_path

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
export PATH="$VENV_DIR/bin:$PATH"

info "Validando runtime final..."
"$PYTHON" - <<'PY'
import sys
assert sys.version_info[:2] == (3, 11), sys.version
print(f"Python final: {sys.version.split()[0]}")
PY

info "Atualizando pip, setuptools e wheel..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel

info "Instalando dependências do projeto..."
"$PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
"$PYTHON" -m pip install -e "$PROJECT_DIR"
"$PYTHON" -m pip check

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

Python do sistema selecionado para o Agent IA:
  $PYTHON_BIN
  $($PYTHON_BIN --version 2>&1)

Python efetivo do Agent IA:
  $PYTHON
  $($PYTHON --version 2>&1)

Ambiente virtual:
  $VENV_DIR

PATH atual:
  $VENV_DIR/bin já foi colocado no início do PATH desta instalação.

Novos terminais:
  $HOME/.profile contém o bloco gerenciado do Agent IA.

Ativar manualmente:
  source "$VENV_DIR/bin/activate"

Iniciar a interface sem ativar o venv:
  bash "$PROJECT_DIR/scripts/start_web.sh"

Abrir no navegador:
  http://localhost:8080/ui

Antes do primeiro uso, revise:
  $PROJECT_DIR/.env
EOF