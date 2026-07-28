# Preparação no WSL

Quando o projeto fica em `/mnt`, o filesystem montado do Windows pode não suportar os links simbólicos usados pelo `venv`. O erro normalmente aparece assim:

```text
Operation not supported: 'lib' -> '.venv/lib64'
```

O instalador cria o ambiente virtual no filesystem Linux do usuário, em:

```text
~/.venvs/projeto-agent-ia-interface
```

O código permanece normalmente em `/mnt/Projetos/projeto-agent-ia-interface`.

## Instalação automática

Na raiz do projeto:

```bash
cd /mnt/Projetos/projeto-agent-ia-interface
git pull origin main
bash scripts/setup_wsl.sh
```

O script:

- remove apenas um `.venv` incompleto dentro do projeto;
- instala `python3-full` e `python3-venv` pelo `apt` quando necessário;
- cria ou reutiliza o ambiente virtual em `~/.venvs`;
- atualiza `pip`, `setuptools` e `wheel`;
- instala `requirements.txt` e o projeto em modo editável;
- cria `.env` a partir de `.env.example` quando ele ainda não existe;
- adiciona os valores básicos da interface sem substituir configurações existentes;
- valida a instalação e a versão do comando `agent`.

Para recriar completamente o ambiente:

```bash
bash scripts/setup_wsl.sh --recreate
```

Um local diferente pode ser definido assim:

```bash
AGENT_VENV_DIR="$HOME/ambientes/agent-ia" bash scripts/setup_wsl.sh
```

## Iniciar a interface

Não é necessário ativar o ambiente virtual:

```bash
bash scripts/start_web.sh
```

Acesse:

```text
http://localhost:8080/ui
```

Para usar os comandos manualmente no terminal:

```bash
source "$HOME/.venvs/projeto-agent-ia-interface/bin/activate"
agent --version
agent-web
```

## Configuração

Antes do primeiro uso, revise o arquivo `.env`, principalmente:

- `POSTGRES_DSN`;
- `REDIS_URL`;
- credenciais e chaves SSH;
- bastion/VPN;
- provedor principal de IA;
- segunda IA revisora;
- redes autorizadas em `AGENT_UI_ALLOWED_NETWORKS`.

O instalador não utiliza `--break-system-packages` e não modifica o Python global com `pip`.
