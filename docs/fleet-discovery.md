# Fleet Discovery do NOC autônomo

A descoberta de frota existe para resolver a primeira etapa do NOC em escala sem exigir que o operador cadastre manualmente quais máquinas são monitoramento, produção, standby ou hosts mistos.

## Princípio

Na primeira execução do worker, o Fleet Discovery percorre toda a faixa privada autorizada, tenta exatamente um acesso com a credencial operacional configurada e coleta somente uma impressão digital de leitura.

O padrão inicial é:

```text
172.27.0.0/16
```

A faixa é configurável por `FLEET_DISCOVERY_CIDRS`. Redes públicas são rejeitadas pelo código.

A varredura não é feita de uma vez. Ela usa lotes com cursor persistido no PostgreSQL. Se o serviço reiniciar no meio, a próxima execução continua do último ponto salvo.

## O que é salvo

Duas tabelas novas são criadas automaticamente:

- `fleet_discovery_runs`: progresso, cursor e contadores de cada ronda completa;
- `fleet_assets`: situação conhecida de cada IP testado.

Para cada alvo, a base mantém, quando disponível:

- IP VPN e porta SSH;
- nome do cliente vindo do menu VPN;
- hostname e sistema operacional;
- status do acesso (`ok`, `timeout`, `auth_failed`, `not_found` ou `error`);
- papéis detectados;
- ambiente classificado;
- capacidades encontradas;
- presença de Checkmk/OMD;
- confiança de que o host exerce papel de monitoramento;
- sites OMD encontrados;
- evidências técnicas usadas na classificação;
- último acesso bem-sucedido;
- quantidade de falhas consecutivas.

Hosts acessados com sucesso também são sincronizados com a tabela `hosts` já existente, de forma que os Agents atuais passam a reutilizar esse inventário automaticamente.

## Servidor misto

Monitoramento não é tratado como sinônimo de máquina dedicada.

Um host pode ser salvo, por exemplo, como:

```json
{
  "environment": "production",
  "roles": ["monitoring", "production"],
  "monitoring_detected": true,
  "monitoring_confidence": 100
}
```

Isso permite usar o Checkmk que roda dentro de um servidor de produção como fonte de observação sem liberar self-healing de monitoramento nesse servidor. A política de mudança continua respeitando o ambiente `production`.

Se o Checkmk for identificado tecnicamente, mas não houver evidência suficiente para distinguir monitor dedicado de host misto, o papel `monitoring` é registrado, porém o ambiente permanece `unknown`. Esse comportamento é intencionalmente conservador.

## Impressão digital de leitura

Depois que o acesso é estabelecido, a descoberta coleta sinais como:

```text
hostname
/etc/os-release
hostname -I
/omd/sites
omd sites
cmk --version
containers Checkmk
processos CMC/Nagios/mkeventd/rrdcached/Checkmk
```

Nenhuma correção é executada durante a descoberta.

A classificação de monitoramento usa múltiplas evidências. Por exemplo:

- `/omd/sites` presente;
- sites OMD encontrados;
- `cmk` encontrado;
- container Checkmk encontrado;
- processos Checkmk/OMD encontrados;
- nome do host/cliente indicando monitoramento.

Um único sinal não é suficiente para liberar mudança automática.

## Escala e retomada

Configuração padrão:

```text
FLEET_DISCOVERY_ENABLED=true
FLEET_DISCOVERY_AUTO_START=true
FLEET_DISCOVERY_CIDRS=172.27.0.0/16
FLEET_DISCOVERY_BATCH_SIZE=128
FLEET_DISCOVERY_CONCURRENCY=8
FLEET_DISCOVERY_CONNECT_TIMEOUT_SECONDS=8
FLEET_DISCOVERY_COMMAND_TIMEOUT_SECONDS=10
FLEET_DISCOVERY_RESCAN_HOURS=24
FLEET_DISCOVERY_MAX_HOSTS=65536
FLEET_DISCOVERY_LOOP_SLEEP_SECONDS=2
FLEET_DISCOVERY_MONITOR_THRESHOLD=50
```

O worker inicia uma thread dedicada para a descoberta. Assim a varredura não ocupa o loop que processa incidentes e investigações.

O PostgreSQL usa um advisory lock para impedir que dois workers executem o mesmo lote ao mesmo tempo.

## Depois da descoberta inicial

A sequência operacional fica:

```text
Faixa privada autorizada
  -> Fleet Discovery completo
  -> PostgreSQL
       -> hosts acessíveis
       -> hosts não acessados
       -> papéis/capacidades
       -> Checkmk/OMD encontrados
  -> Agents reutilizam o inventário persistido
  -> rediscovery periódica
```

A próxima camada do Fleet Controller pode então concentrar a ronda de saúde nos ativos que possuem a capacidade `checkmk`, inclusive quando o Checkmk estiver hospedado em um servidor misto.

## Visualização e diagnóstico

A API web expõe:

```text
GET  /ui/api/noc/fleet
POST /ui/api/noc/fleet/tick
```

O primeiro endpoint retorna o progresso da ronda e a lista recente dos alvos que não puderam ser acessados.

No CLI do worker:

```bash
agent-worker fleet-status
```

mostra a mesma visão, incluindo os IPs com `timeout`, `auth_failed`, `not_found` ou `error`.
