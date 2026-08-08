# Fleet Discovery do NOC autônomo

A descoberta de frota existe para resolver a primeira etapa do NOC em escala sem exigir que o operador cadastre manualmente quais máquinas são monitoramento, produção, standby ou hosts mistos.

## Princípio

Subir ou reiniciar o `agent-worker` **não inicia uma varredura completa nova**. A descoberta inicial começa somente quando o operador clica em **Iniciar descoberta completa** na Central NOC.

Depois de iniciada, ela roda em segundo plano até terminar toda a faixa. O cursor fica persistido no PostgreSQL. Se o serviço reiniciar no meio, o worker identifica a ronda ativa e retoma automaticamente do último ponto salvo.

O padrão inicial é:

```text
172.27.0.0/16
```

A faixa é configurável por `FLEET_DISCOVERY_CIDRS`. Redes públicas são rejeitadas pelo código.

A varredura usa lotes e concorrência limitada; não tenta todos os IPs ao mesmo tempo.

## Acompanhamento pela interface

A Central NOC mostra um painel **Mapeamento da frota** com:

- estado `não iniciada`, `em andamento` ou `inventário concluído`;
- percentual da descoberta;
- quantidade processada e total;
- acessíveis;
- não acessados;
- Checkmks encontrados;
- última atualização/heartbeat;
- aviso de possível travamento quando uma ronda ativa deixa de atualizar o cursor;
- ativos mapeados recentemente;
- nome operacional vindo do Monitor 1;
- IP VPN;
- hostname técnico;
- papel/ambiente;
- sites Checkmk encontrados;
- lista dos IPs não acessados e o motivo.

A página consulta o status a cada poucos segundos. Fechar a tela não interrompe a descoberta.

## Identidade operacional do ativo

O nome principal apresentado e reutilizado pelos Agents é o nome retornado pelo inventário do Monitor 1 durante o fluxo `vpn <IP>`, e não o hostname Linux do destino.

Exemplos:

```text
HOTBEL MONITOR       172.27.232.153
HOTBEL PROD          172.27.232.154
HOTBEL STANDBY       172.27.232.155
CACIQUE MONITOR      172.27.229.100
```

Quando o menu retorna nomes com parênteses, como `HOTBEL (MONITOR)`, a aplicação mantém o valor operacional normalizado como `HOTBEL MONITOR` para exibição e pesquisa. O hostname real coletado dentro da máquina continua armazenado separadamente como informação técnica.

A prioridade de identidade é:

```text
nome do inventário VPN / Monitor 1
        ↓
hostname real do sistema
        ↓
IP VPN
```

## O que é salvo

Duas tabelas são usadas:

- `fleet_discovery_runs`: progresso, cursor e contadores de cada descoberta completa;
- `fleet_assets`: situação conhecida de cada IP testado.

Para cada alvo, a base mantém, quando disponível:

- IP VPN e porta SSH;
- nome operacional do cliente/servidor vindo do menu VPN do Monitor 1;
- hostname real do sistema, separadamente;
- sistema operacional;
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

Hosts acessados com sucesso também são sincronizados com a tabela `hosts` já existente. Nessa sincronização, o nome do Monitor 1 tem prioridade sobre o hostname real.

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

Se o Checkmk for identificado tecnicamente, mas não houver evidência suficiente para distinguir monitor dedicado de host misto, o papel `monitoring` é registrado, porém o ambiente permanece `unknown`.

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

## Depois que a descoberta chega a 100%

A operação muda de **Discovery** para **Patrol**.

O Fleet Patrol é iniciado pelo worker e permanece aguardando o inventário inicial. Assim que a primeira descoberta termina, ele passa a executar automaticamente em ciclos, sem novo clique do operador.

Por padrão:

```text
FLEET_PATROL_ENABLED=true
FLEET_PATROL_INTERVAL_SECONDS=300
FLEET_PATROL_CONCURRENCY=4
FLEET_PATROL_COMMAND_TIMEOUT_SECONDS=45
FLEET_PATROL_MAX_MONITORS=500
```

A ronda não percorre novamente os 65 mil IPs. Ela seleciona somente `fleet_assets` que foram classificados com capacidade Checkmk e consulta os sites/containers encontrados.

Cada monitor é consultado em lote pelo Livestatus para localizar:

```text
hosts WARN/CRIT/UNKNOWN/DOWN
services WARN/CRIT/UNKNOWN
```

Quando encontra uma anomalia:

```text
Fleet Patrol
  -> correlaciona host_name/host_address com o inventário aprendido
  -> cria/atualiza MonitoringMapping quando possível
  -> Incident Manager
  -> deduplicação/flapping
  -> fila Redis
  -> Agent especialista
  -> Supervisor NOC
  -> watcher/correção/escalonamento
```

Se uma anomalia que existia na ronda anterior deixa de aparecer, o Fleet Patrol registra recovery para o mesmo `site + host + service`.

O botão **Rodar ronda agora** existe apenas para teste/uso pontual. A operação normal não depende dele.

## Descoberta completa x ronda

São processos diferentes:

```text
DESCOBERTA COMPLETA
- pesada
- percorre a faixa autorizada
- constrói/atualiza inventário
- iniciada manualmente
- retoma automaticamente se houver restart

RONDA AUTOMÁTICA
- leve
- usa somente o inventário pronto
- consulta Checkmks encontrados
- roda periodicamente sozinha
- cria incidentes automaticamente
```

Uma nova descoberta completa pode ser iniciada manualmente quando houver uma mudança estrutural grande. Ela não é disparada toda vez que alguém abre uma análise ou reinicia o serviço.

## Parâmetros da descoberta

```text
FLEET_DISCOVERY_ENABLED=true
FLEET_DISCOVERY_CIDRS=172.27.0.0/16
FLEET_DISCOVERY_BATCH_SIZE=128
FLEET_DISCOVERY_CONCURRENCY=8
FLEET_DISCOVERY_CONNECT_TIMEOUT_SECONDS=8
FLEET_DISCOVERY_COMMAND_TIMEOUT_SECONDS=10
FLEET_DISCOVERY_MAX_HOSTS=65536
FLEET_DISCOVERY_LOOP_SLEEP_SECONDS=2
FLEET_DISCOVERY_MONITOR_THRESHOLD=50
```

O PostgreSQL usa advisory lock para impedir que dois workers executem o mesmo lote de descoberta ao mesmo tempo.

## Nova análise continua existindo

O campo **Nova análise** não é removido. Ele continua disponível para uma necessidade pontual em que o operador queira informar um IP/objetivo e investigar imediatamente.

Ele não inicia Fleet Discovery e não é necessário para a ronda normal do NOC.

## API e CLI

A API web expõe:

```text
GET  /ui/api/noc/fleet
POST /ui/api/noc/fleet/start
POST /ui/api/noc/fleet/tick
POST /ui/api/noc/fleet/patrol
```

No CLI:

```bash
agent-worker fleet-status
```

mostra descoberta, ativos mapeados, alvos não acessados e o estado da ronda automática.
