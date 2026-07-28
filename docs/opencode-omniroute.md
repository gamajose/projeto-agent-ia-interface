# OpenCode via OmniRoute no Agent IA

## Papel de cada componente

O OpenCode é um agente de desenvolvimento. Ele lê o projeto, responde perguntas sobre código, propõe alterações e pode executar ferramentas locais mediante as permissões configuradas.

O OmniRoute continua sendo o gateway de modelos. O OpenCode não recebe as chaves reais dos provedores; ele usa somente:

```env
OMNIROUTE_API_KEY=CHAVE_DO_ENDPOINT
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
```

Arquitetura:

```text
OpenCode TUI / Web
        ↓ API OpenAI-compatible
OmniRoute 127.0.0.1:20128/v1
        ├── auto/coding
        ├── auto/fast
        ├── auto/cheap
        └── demais modelos, rotas ou combos
```

O motor de troubleshooting do Agent IA continua separado. O OpenCode não herda automaticamente:

- conexão SSH do Agent;
- senha de servidor;
- bastion/VPN;
- aprovação corretiva;
- permissão para acessar banco de cliente.

## Instalação automatizada

Na raiz do projeto:

```bash
bash scripts/setup_wsl.sh
bash scripts/setup_opencode.sh
```

O segundo script:

1. instala ou atualiza `opencode-ai` pelo npm;
2. grava somente variáveis ausentes no `.env`;
3. gera `~/.config/opencode/opencode.json` sem incluir o token;
4. instala `opencode-web.service`;
5. inicia a interface web em `127.0.0.1:4096`;
6. gera uma senha inicial quando ela ainda não existe.

## Configuração gerada

O arquivo `opencode.json` usa um provedor customizado:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "omniroute/auto/coding",
  "small_model": "omniroute/auto/fast",
  "enabled_providers": ["omniroute"],
  "provider": {
    "omniroute": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OmniRoute",
      "options": {
        "baseURL": "{env:OPENCODE_OMNIROUTE_BASE_URL}",
        "apiKey": "{env:OMNIROUTE_API_KEY}",
        "timeout": 600000
      },
      "models": {
        "auto/coding": {"name": "OmniRoute · Código"},
        "auto/fast": {"name": "OmniRoute · Rápido"}
      }
    }
  }
}
```

As rotas são montadas a partir de `OMNIROUTE_ROUTES`. O modelo principal pode ser alterado com:

```env
OPENCODE_MODEL=auto/coding
OPENCODE_SMALL_MODEL=auto/fast
```

## Permissões adotadas

A configuração do projeto usa:

```json
{
  "default_agent": "plan",
  "share": "disabled",
  "permission": {
    "edit": "ask",
    "bash": "ask",
    "external_directory": "deny",
    "webfetch": "ask",
    "websearch": "ask"
  }
}
```

O modo padrão é `plan`. Para alterar arquivos, o operador pode mudar para o agente `build`, mas edição e shell continuam pedindo confirmação.

## Usar pelo terminal

```bash
source /home/jose/.venvs/projeto-agent-ia-interface/bin/activate
agent-opencode
```

Também pode usar diretamente:

```bash
cd /mnt/Projetos/projeto-agent-ia-interface
opencode
```

Ao executar diretamente, exporte as variáveis ou use `agent-opencode`, que carrega o `.env` do projeto e o arquivo de configuração correto.

## Usar pela aplicação web

A seção **OpenCode** mostra:

- versão instalada;
- executável localizado;
- rota padrão;
- endpoint OmniRoute;
- diretório do projeto;
- estado do serviço web;
- comando de túnel SSH.

O botão **Abrir OpenCode** funciona depois que o túnel estiver ativo no computador do operador.

Para a VM atual:

```powershell
ssh -N `
  -L 20128:127.0.0.1:20128 `
  -L 4096:127.0.0.1:4096 `
  jose@192.168.28.10 -p 2222
```

Acesse:

```text
Agent IA:  http://192.168.28.10:8081/ui
OmniRoute: http://127.0.0.1:20128
OpenCode:  http://127.0.0.1:4096
```

O usuário padrão da autenticação HTTP é:

```text
opencode
```

A senha fica somente no `.env`:

```env
OPENCODE_SERVER_PASSWORD=VALOR_GERADO
```

## Operação do serviço

```bash
sudo systemctl status opencode-web --no-pager -l
sudo journalctl -u opencode-web -n 100 --no-pager
sudo systemctl restart opencode-web
```

Validar a configuração sem mostrar segredo:

```bash
source /home/jose/.venvs/projeto-agent-ia-interface/bin/activate
agent-opencode --status
```

Regenerar o JSON após alterar rotas:

```bash
agent-opencode --configure
sudo systemctl restart opencode-web
```

## Segurança

- a porta 4096 permanece em `127.0.0.1`;
- o acesso remoto deve ser feito pelo túnel SSH;
- a interface web exige senha;
- o token do OmniRoute não é gravado no `opencode.json`;
- o compartilhamento de sessões fica desabilitado;
- edição e bash exigem confirmação;
- diretórios externos ao projeto ficam bloqueados;
- o OpenCode não substitui as políticas do Agent IA.
