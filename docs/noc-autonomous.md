# Supervisor NOC autônomo

A partir da versão 1.34.0, o webhook do Checkmk deixa de funcionar apenas como disparador de troubleshooting e passa a alimentar um ciclo de incidente supervisionado.

## Objetivo

O operador não precisa iniciar manualmente uma investigação para cada repetição do mesmo alerta. O Supervisor NOC mantém o estado do incidente no Redis, deduplica notificações, correlaciona mudanças de estado, detecta flapping, dispara a primeira investigação e acompanha o job até a etapa em que o operador realmente precisa intervir.

O fluxo é:

```text
Checkmk
  -> webhook
  -> Incident Manager
  -> deduplicação / correlação / flapping
  -> fila do Agent IA
  -> acesso SSH/VPN
  -> investigação adaptativa
  -> IA crítica
  -> proposta/correção conforme política
  -> acompanhamento
  -> recovery OK/UP do Checkmk
  -> incidente resolvido
```

## Ciclo de vida

Os estados operacionais do Supervisor NOC são:

- `new`: incidente criado;
- `queued`: investigação enviada ao worker;
- `investigating`: worker executando a investigação;
- `awaiting_approval`: existe correção revisada aguardando aprovação humana;
- `watching`: análise/correção concluída e o Supervisor aguarda normalização do Checkmk;
- `needs_attention`: a IA terminou sem condição de resolver sozinha ou o job falhou;
- `resolved`: o Checkmk enviou recuperação ou o operador encerrou manualmente.

`flapping` é uma propriedade independente do estado. Um incidente pode, por exemplo, estar `investigating` e ao mesmo tempo possuir `flapping=true`.

## Deduplicação

A chave lógica do incidente é formada por:

```text
site + host + service
```

Enquanto existir um incidente aberto para a mesma chave, novas notificações não criam novas investigações. O evento continua sendo registrado, o contador de ocorrências aumenta e a saída mais recente substitui a anterior.

Por padrão, notificações idênticas recebidas em até 300 segundos são marcadas como deduplicadas.

## Flapping

O Supervisor mantém também um histórico por `site + host + service`, inclusive entre um incidente resolvido e a próxima abertura. Isso permite detectar ciclos como:

```text
CRIT -> OK -> CRIT -> OK -> CRIT
```

Por padrão, quatro transições entre `ok` e `problem` dentro de 30 minutos marcam o serviço como flapping.

A missão enviada à IA recebe essa informação explicitamente para que ela priorize correlação de rota, VPN, interface, serviço, rede e dependências em vez de tratar cada CRIT como uma ocorrência isolada.

## Recuperação

Estados `OK`, `UP`, `0`, `RECOVERY` e `RECOVERED` são tratados como recuperação.

Uma recuperação nunca abre uma investigação nova. Se houver incidente ativo, ele é encerrado com `resolution_source=checkmk_recovery`.

Para esse comportamento funcionar de ponta a ponta, a regra de notificação do Checkmk deve enviar ao webhook tanto eventos de problema quanto eventos de recuperação.

## Segurança

O Supervisor NOC não remove as políticas existentes do Agent IA.

- produção e standby continuam sujeitos às regras de ambiente;
- ferramentas corretivas continuam limitadas pelos playbooks/políticas;
- a IA crítica continua validando a conclusão;
- aprovação humana continua disponível para ações que a política exige;
- reboot, shutdown, banco de cliente e demais operações bloqueadas continuam bloqueadas;
- `CHECKMK_WEBHOOK_AUTO_CORRECT` permanece `false` por padrão.

Se o Redis do Supervisor NOC falhar, o webhook entra em modo degradado e mantém o troubleshooting antigo disponível. O gerenciador de incidentes não deve se tornar um ponto único de falha.

## Configuração

Os valores padrão podem ser sobrescritos no `.env`/Vault:

```dotenv
NOC_INCIDENT_ENABLED=true
NOC_INCIDENT_PREFIX=agent-ia:noc
NOC_INCIDENT_TTL_SECONDS=604800
NOC_INCIDENT_DEDUP_SECONDS=300
NOC_FLAPPING_WINDOW_SECONDS=1800
NOC_FLAPPING_TRANSITION_THRESHOLD=4
NOC_AUTO_INVESTIGATE=true
NOC_AUTO_CLOSE_ON_OK=true
```

O TTL padrão mantém o estado e o histórico recente por sete dias.

## Webhook do Checkmk

O contrato permanece compatível:

```bash
curl -X POST http://127.0.0.1:8080/webhooks/checkmk \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-Token: TOKEN_EXCLUSIVO_DO_CHECKMK' \
  -d '{
    "host":"checkmk-cliente",
    "service":"Check_MK Agent",
    "state":"CRIT",
    "output":"Connection refused",
    "site":"cliente",
    "environment":"monitoring",
    "auto_correct":false
  }'
```

A primeira ocorrência retorna também o objeto `incident` e `investigation_started=true`. Repetições deduplicadas retornam `investigation_started=false`.

Na recuperação:

```json
{
  "host": "checkmk-cliente",
  "service": "Check_MK Agent",
  "state": "OK",
  "output": "Agent responded",
  "site": "cliente",
  "environment": "monitoring"
}
```

o incidente correspondente é encerrado automaticamente.

## API da interface

O módulo já registrado em `web_main.py` expõe:

```text
GET  /ui/api/noc/dashboard
GET  /ui/api/noc/incidents
GET  /ui/api/noc/incidents/{id}
POST /ui/api/noc/incidents/{id}/acknowledge
POST /ui/api/noc/incidents/{id}/resolve
```

O dashboard retorna contadores para incidentes ativos, fila, investigação, aprovação, acompanhamento, atenção, flapping e resolvidos no dia.

A consulta de um incidente sincroniza o status do job Redis com o ciclo operacional. Assim, ao abrir o painel, um job que terminou passa automaticamente para `watching`, `awaiting_approval` ou `needs_attention` conforme o resultado da IA.

## Próximas extensões

A base foi criada para receber, sem mudar o contrato do incidente:

- revalidação ativa pela API do Checkmk;
- Communication Agent para ticket/WhatsApp/escalonamento;
- especialistas SNMP/BMC;
- política de self-healing por classe de serviço;
- correlação entre vários hosts do mesmo cliente;
- dashboard visual do NOC com atualização em tempo real.
