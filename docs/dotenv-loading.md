# Carregamento seguro do `.env`

O script `scripts/start_web.sh` lê o arquivo `.env` com `python-dotenv` pelo Python do ambiente virtual.

Esse formato aceita valores com espaços, por exemplo:

```env
AGENT_UI_OPERATOR_NAME=José Luiz
AGENT_UI_PORT=8081
```

O arquivo não é mais executado pelo Bash com `source`, evitando que trechos após espaços sejam interpretados como comandos.

Também continua sendo possível indicar outro arquivo:

```bash
AGENT_ENV_FILE=/caminho/alternativo.env bash scripts/start_web.sh
```
