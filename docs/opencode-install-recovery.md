# Recuperação da instalação do OpenCode

Se o npm instalar o pacote, mas o Agent IA continuar exibindo `Não instalado`, valide:

```bash
command -v opencode
opencode --version
```

O instalador deve:

- autorizar o `postinstall` do pacote `opencode-ai`;
- localizar o executável pelo `npm prefix -g` ou por `~/.local/bin`;
- gravar `OPENCODE_ENABLED=true` e `OPENCODE_CLI_PATH` no `.env`;
- criar e iniciar `opencode-web.service`;
- exibir a linha exata em caso de falha.

Para instalações legadas:

```bash
AGENT_ENV_FILE=/opt/agent-ia/app/.env \
AGENT_VENV=/opt/agent-ia/venv \
bash scripts/setup_opencode.sh
```
