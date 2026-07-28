# Execução em lote pela interface

A interface aceita um alvo individual, uma lista digitada ou um arquivo de alvos. Cada entrada é enviada ao mesmo motor de investigação usado pelo fluxo individual.

## Comportamento operacional

- Todos os alvos usam o bastion SSH/VPN configurado no `.env`.
- Cada servidor recebe conexão, classificação de ambiente, investigação, memória e resultado próprios.
- Uma falha de acesso em um servidor não interrompe os demais.
- A concorrência é limitada por `AGENT_BATCH_CONCURRENCY`.
- Produção e standby continuam somente com investigação e proposta.
- Não existe aprovação coletiva. Qualquer ação segura é revisada e aprovada individualmente por investigação.

## Lista digitada

O campo de alvo aceita ponto e vírgula, vírgula ou uma entrada por linha:

```text
172.27.232.10; 172.27.232.11
monitor-cliente
servidor-porta-alternativa:2222
```

Para IPv6 com porta, use colchetes:

```text
[fd00::20]:2222
```

## TXT

Um arquivo TXT pode conter a mesma lista:

```text
# Servidores do cliente
172.27.229.10
172.27.229.11:2222
monitor-cliente
```

## CSV com ponto e vírgula

```csv
target;hostname;porta;ambiente;objetivo;playbook
172.27.229.10;srv-prod-01;22;production;Validar agente Checkmk;checkmk-agent-port
172.27.229.11;srv-std-01;2222;standby;Validar filesystem;linux-filesystem
```

Cabeçalhos aceitos, em português ou inglês:

- `target`, `alvo`, `ip`, `vpn_ip`
- `hostname`, `host`, `servidor`
- `site`
- `porta`, `port`, `ssh_port`
- `ambiente`, `environment`
- `objetivo`, `objective`, `problema`
- `modo`, `mode`
- `provedor`, `provider`, `ia`
- `modelo`, `model`, `rota`
- `playbook`, `playbook_id`
- `modo_playbook`, `playbook_mode`

Colunas de senha, token ou credencial não são aceitas e são ignoradas.

## YAML

O YAML de lote usa `defaults` e `targets`. Ele é diferente do playbook de diagnóstico armazenado em `config/playbooks`.

```yaml
defaults:
  environment: monitoring
  objective: Validar comunicação do agente Checkmk
  provider: auto
  playbook_id: checkmk-agent-port

targets:
  - target: 172.27.232.20
    hostname: monitor-a
    ssh_port: 22

  - ip: 172.27.232.21
    hostname: monitor-b
    porta: 2222
    objetivo: Validar porta 6556 e socket do agente
```

Quando `playbook_id` é informado, o modo de playbook passa a `manual` para aquele alvo. Os demais valores da tela funcionam como padrão e são substituídos pelos campos específicos de cada servidor.

## JSON

```json
{
  "defaults": {
    "environment": "production",
    "objective": "Validar saúde geral sem executar alterações"
  },
  "targets": [
    {"target": "172.27.229.10", "ssh_port": 22},
    {"target": "172.27.229.11", "ssh_port": 2222}
  ]
}
```

Também é possível usar uma string simples:

```json
{
  "defaults": {"environment": "training"},
  "targets": "192.0.2.10;192.0.2.11"
}
```

## Configuração

```env
AGENT_BATCH_ENABLED=true
AGENT_BATCH_MAX_TARGETS=50
AGENT_BATCH_CONCURRENCY=2
AGENT_BATCH_MAX_FILE_BYTES=1000000
```

Em `AGENT_EXECUTION_MODE=queue`, as entradas são enfileiradas e acompanhadas pela interface. Em modo `inline`, a interface mantém até o número configurado de requisições simultâneas.
