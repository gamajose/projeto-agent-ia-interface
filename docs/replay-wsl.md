# Visualização e replay no WSL

A versão experimental 1.28.0 permite percorrer a interface e as decisões multi-host sem conectar em clientes.

## Proteções do replay

- não abre SSH;
- não executa o menu VPN;
- não usa credenciais do `.env`;
- não acessa banco de cliente;
- não cria autorização corretiva;
- usa somente empresas, IPs, hostnames e saídas fictícias.

## Iniciar a aplicação local

Na raiz do repositório:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Use um PostgreSQL local de desenvolvimento e, opcionalmente, Redis. Sem Redis, o acompanhamento funciona em memória.

```bash
export POSTGRES_DSN='postgresql+psycopg://agent_ia:agent_ia@127.0.0.1:5432/agent_ia'
export AGENT_REPLAY_ENABLED=true
export AGENT_UI_HOST=127.0.0.1
export AGENT_UI_PORT=8080
python -m app.db.init_db
agent-web
```

Abra `http://127.0.0.1:8080/ui/` e selecione **Replay WSL**.

## Cenários incluídos

- Checkmk parcialmente indisponível;
- flapping de VPN;
- alerta no standby com causa no monitoramento;
- timeout SNMP por camadas.

Cada replay publica os mesmos eventos SSE usados por uma investigação real, incluindo comandos, mudança de host, progresso e resultado por abas.

## Fluxo guiado

A investigação normal foi organizada em quatro etapas:

1. cliente e alerta;
2. escopo e rota;
3. estratégia;
4. revisão e proteções.

As opções avançadas continuam disponíveis, mas não ocupam o primeiro contato do operador com o formulário.

## Importante

O replay valida interface, navegação, apresentação e continuidade do acompanhamento. Ele não substitui a validação de conectividade, credenciais, comportamento do menu VPN ou comandos em um ambiente autorizado.
