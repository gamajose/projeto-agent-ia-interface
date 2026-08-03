# Atualização completa da pilha de IA no WSL

O WSL pode usar dois layouts suportados:

- repositório direto em `/opt/agent-ia`;
- instalação portátil com código em `/opt/agent-ia/app`.

O comando recomendado detecta o layout, preserva o `.env`, corrige caminhos antigos,
instala dependências, prepara Ollama e sobe OmniRoute:

```bash
cd /opt/agent-ia
bash scripts/update_wsl.sh
```

O script interrompe a atualização quando existem alterações locais. Ele nunca executa
`git reset`, `git clean` ou descarte automático.

## O que é preservado

- chaves Gemini, Groq, DeepSeek e OpenRouter;
- senhas SSH e bastion;
- PostgreSQL e Redis existentes;
- configurações da interface;
- modelos já baixados no Ollama;
- dados persistentes do OmniRoute.

O sincronizador adiciona somente variáveis novas ausentes e corrige caminhos gerenciados,
como `AI_SETTINGS_ENV_PATH`, `AGENT_VENV_DIR`, `OMNIROUTE_ENV_FILE` e diretórios de trabalho.
Antes de alterar o `.env`, cria um backup com timestamp.

## Somente preparar a pilha de IA

```bash
cd /opt/agent-ia
bash scripts/setup_ai_stack.sh
```

Opções úteis:

```bash
bash scripts/setup_ai_stack.sh --ollama-model llama3.2:1b
bash scripts/setup_ai_stack.sh --without-ollama
bash scripts/setup_ai_stack.sh --without-omniroute
bash scripts/setup_ai_stack.sh --with-opencode
```

O OpenCode é opcional porque depende de Node.js 20 ou superior e instalação npm no usuário
da aplicação. Ollama e OmniRoute são instalados por padrão.

## Validação

```bash
sudo systemctl status ollama.service --no-pager -l
sudo docker ps --filter name=omniroute
sudo systemctl status agent-ia-web.service --no-pager -l
curl -fsS -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8080/ui
```

A interface também apresenta o diagnóstico dos provedores depois da sincronização. Os
valores das chaves nunca são impressos pelo instalador.
