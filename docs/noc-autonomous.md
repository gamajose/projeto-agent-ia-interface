# NOC autônomo do Agent IA

O Agent IA opera incidentes do Checkmk de ponta a ponta: recebe o evento, correlaciona repetições, investiga, usa especialistas, aplica self-healing de baixo risco quando a política permite, força nova checagem no Checkmk, acompanha até ficar verde, reinvestiga se necessário e prepara/publica comunicação e escalonamento.

O objetivo é deixar o operador como supervisor da automação, e não como executor de cada comando de troubleshooting.

## Fluxo completo

```text
Checkmk
  -> webhook de problema/recovery
  -> Incident Manager
  -> deduplicação + flapping
  -> fila Redis / worker
  -> Access Agent (SSH/VPN / inventário)
  -> investigação adaptativa
       -> Linux / systemd / recursos
       -> Checkmk / OMD / Livestatus
       -> VPN / rede / pfSense
       -> Docker / containers
       -> SNMP v2c/v3
       -> BMC / IPMI / Redfish
  -> RCA + segunda IA crítica
  -> política de autonomia
       -> ação fora do envelope: escalona
       -> ação segura aprovada: self-healing L4
  -> pós-validação local
  -> forced service check no Checkmk
  -> watcher Livestatus
       -> OK: resolve
       -> continua não-OK: revalida
       -> persiste: reinvestiga
       -> sem solução segura: needs_attention + escalonamento
  -> Communication Agent
  -> memória operacional / playbook candidato
```

## Estados do incidente

- `new`: primeira ocorrência recebida;
- `queued`: investigação aguardando worker;
- `investigating`: coleta e raciocínio em andamento;
- `awaiting_approval`: há proposta segura, mas o ambiente/política não permite execução autônoma;
- `watching`: a análise/correção terminou e o supervisor acompanha o estado real no Checkmk;
- `needs_attention`: a automação chegou a um limite seguro e precisa de operador/Infra/N3/cliente;
- `resolved`: recuperação confirmada por webhook, Livestatus ou encerramento manual.

`flapping=true` é uma propriedade adicional e pode coexistir com qualquer estado aberto.

## Deduplicação e flapping

A chave lógica é:

```text
site + host + service
```

Enquanto existir incidente aberto, novas notificações não disparam investigações duplicadas. Elas atualizam a saída, ocorrência e linha do tempo.

O histórico por fingerprint é mantido mesmo após recovery. Assim ciclos como:

```text
CRIT -> OK -> CRIT -> OK -> CRIT
```

são reconhecidos e entram como fato explícito na missão da IA. O agente passa a priorizar correlação de VPN, rota, vizinhança, interface, gateway, dpinger, processo e endpoint, em vez de tratar cada CRIT isoladamente.

## Autonomia operacional

O supervisor usa níveis de autonomia. A configuração padrão desta versão é L4 para ambientes autorizados.

| Nível | Comportamento |
| --- | --- |
| L0 | registra e observa |
| L1 | coleta e investiga |
| L2 | conclui RCA e propõe |
| L3 | executa somente após aprovação do operador |
| L4 | executa automaticamente ações de baixo risco já aprovadas pela segunda IA |
| L5 | reservado para expansão futura; não remove guardrails centrais |

Para uma ação L4 ocorrer, todas as condições abaixo precisam ser verdadeiras:

1. ambiente efetivamente classificado como `monitoring` ou `training`;
2. confiança mínima configurada;
3. playbook permitiu a ferramenta corretiva;
4. a segunda IA aprovou a correção;
5. a investigação gerou token de aprovação válido;
6. todas as ferramentas propostas pertencem à allowlist `NOC_SELF_HEAL_TOOLS`;
7. a política central de correção não bloqueou o comando.

A implementação reutiliza o mesmo fluxo de aprovação, recuperação adaptativa, pré-condições e pós-validação usado pela execução manual. O Supervisor não possui um atalho separado para shell.

## Self-healing permitido

A allowlist padrão contém apenas:

```text
systemd.recover_unit
checkmk.recover_omd_service
```

Ela cobre componentes de monitoramento autorizados, como Check_MK Agent/xinetd/SNMP daemon e processos internos OMD permitidos pela política.

Continuam explicitamente fora do self-healing automático:

- reboot, shutdown ou poweroff;
- banco de dados e listeners de banco;
- apagar, truncar, formatar ou reparar filesystem;
- alteração de firewall;
- instalação/remoção de pacote;
- edição arbitrária de configuração;
- `docker restart/start/stop/rm` e demais lifecycle de containers;
- serviços de aplicação do cliente;
- qualquer mudança em produção/standby que a política de ambiente não autorize.

## Checkmk Watcher: acompanhar até ficar verde

Depois de uma correção validada ou de uma análise que indique recuperação, o supervisor não considera o trabalho encerrado apenas porque o comando retornou zero.

Ele resolve o servidor de monitoramento pelo inventário persistido e consulta o serviço pelo Livestatus. Quando possível, dispara um `SCHEDULE_FORCED_SVC_CHECK` e lê novamente:

```text
host_name
service description
state
last_check
plugin_output
```

Estado `0` confirma verde e encerra o incidente.

Caso continue não-OK, o watcher repete em intervalo controlado. Ao atingir o limite de tentativas/tempo, uma nova investigação é enfileirada com a evidência de que a primeira correção não normalizou o serviço. Há limite de reinvestigações para impedir loop infinito.

O recovery recebido diretamente pelo webhook (`OK`, `UP`, `RECOVERY`, `RECOVERED` ou `0`) continua encerrando imediatamente o incidente aberto correspondente.

## Especialista SNMP

O catálogo adaptativo inclui:

- `snmp.transport`: rota, ICMP e UDP/161;
- `snmp.v2.system`: OIDs básicos via v2c;
- `snmp.v3.system`: OIDs básicos via v3;
- `snmp.auto.system`: tenta as credenciais configuradas e registra qual versão respondeu.

As credenciais ficam em Settings/.env/Vault e não são passadas como argumentos pelo navegador. A saída/evidência passa pela redação de segredos antes de persistência ou contexto cognitivo.

O playbook `checkmk-snmp-timeout` cruza o teste protocolar direto com `cmk -vvn`, permitindo diferenciar rota/UDP, credencial, autorização SNMP, configuração do Checkmk e indisponibilidade do equipamento.

Há também `snmp-daemon-stopped`, que pode recuperar `snmpd`/`bsnmpd` somente quando a evidência e a política permitirem.

## Especialista BMC/hardware

O playbook `bmc-hardware-alert` e as ferramentas especialistas coletam:

- fabricante/modelo/serial via DMI;
- identificação e LAN do BMC via IPMI;
- sensores;
- System Event Log (SEL);
- FRU.

O catálogo operacional já inclui Redfish para saúde geral, fontes, temperaturas, fans, storage, event log e rede quando credenciais/endpoint Redfish estiverem configurados.

Falhas físicas não são “corrigidas” pela IA. Elas são classificadas e encaminhadas com evidências.

## Communication Agent e escalonamento

Quando o incidente exige intervenção ou é resolvido, o agente consegue produzir automaticamente:

- atualização de ticket;
- mensagem curta de WhatsApp;
- resumo interno NOC;
- texto de transferência com `Descrição do Problema`, `Ações já realizadas` e `Motivo da Transferência`;
- carta de risco quando a falta de monitoramento ou outro risco operacional justificar.

O roteamento sugere domínio de destino, por exemplo:

- `noc_monitoring`;
- `network`;
- `infra_network_hardware`;
- `infra_hardware`;
- `infra_storage`;
- `infra_os`;
- `infra_n3`.

A geração funciona mesmo sem integração externa. Publicação automática só ocorre se o webhook correspondente estiver explicitamente configurado. Isso evita presumir formato da API de WhatsApp/helpdesk.

## Aprendizado operacional

O fluxo de correção aprovado já gera playbook candidato quando a recuperação termina com pós-validação completa. O rascunho fica vinculado à investigação e continua sujeito à revisão/ativação humana.

O histórico de investigações verificadas permanece disponível para priorizar hipóteses futuras, mas nunca é aceito como prova da causa atual sem evidência nova.

## Worker autônomo

`agent-worker run` agora executa duas responsabilidades no mesmo ciclo:

1. processa jobs da fila;
2. executa o `supervisor_tick` dos incidentes abertos.

Portanto o acompanhamento não depende da tela web estar aberta. A interface apenas observa e permite intervenção quando desejado.

## Configuração

Os nomes abaixo podem ser definidos no `.env` ou no backend de segredos já usado pelo projeto. Segredos não devem ser versionados.

### Incidentes e autonomia

```text
NOC_INCIDENT_ENABLED
NOC_INCIDENT_PREFIX
NOC_INCIDENT_TTL_SECONDS
NOC_INCIDENT_DEDUP_SECONDS
NOC_FLAPPING_WINDOW_SECONDS
NOC_FLAPPING_TRANSITION_THRESHOLD
NOC_AUTO_INVESTIGATE
NOC_AUTO_CLOSE_ON_OK
NOC_AUTONOMY_LEVEL
NOC_SELF_HEAL_ENABLED
NOC_SELF_HEAL_MIN_CONFIDENCE
NOC_SELF_HEAL_TOOLS
NOC_AUTONOMY_MAX_APPROVAL_ROUNDS
```

### Watcher e reinvestigação

```text
NOC_WATCH_INTERVAL_SECONDS
NOC_WATCH_TIMEOUT_SECONDS
NOC_WATCH_MAX_RECHECKS
NOC_REINVESTIGATE_ON_WATCH_FAILURE
NOC_MAX_REINVESTIGATIONS
NOC_CHECKMK_RECHECK_TIMEOUT_SECONDS
```

### Comunicação

```text
NOC_COMMUNICATION_AI_ENABLED
NOC_WHATSAPP_WEBHOOK_URL
NOC_WHATSAPP_WEBHOOK_TOKEN
NOC_WHATSAPP_AUTO_SEND
NOC_INTERNAL_WEBHOOK_URL
NOC_INTERNAL_WEBHOOK_TOKEN
HELPDESK_WEBHOOK_URL
HELPDESK_WEBHOOK_TOKEN
HELPDESK_PUBLISH_AUTOMATICALLY
```

### SNMP

```text
SNMP_V2_COMMUNITY
SNMP_V3_USER
SNMP_V3_AUTH_PASSWORD
SNMP_V3_AUTH_PROTOCOL
SNMP_V3_PRIV_PASSWORD
SNMP_V3_PRIV_PROTOCOL
```

## Webhook do Checkmk

O contrato permanece compatível:

```json
{
  "host": "checkmk-cliente",
  "service": "Check_MK Agent",
  "state": "CRIT",
  "output": "Connection refused",
  "site": "cliente",
  "environment": "monitoring",
  "auto_correct": false
}
```

O header continua sendo `X-Agent-Token` com o segredo dedicado ao webhook.

A primeira ocorrência cria o incidente e dispara a investigação. Repetições ficam deduplicadas. Recovery encerra o mesmo fingerprint.

## API da Central NOC

```text
GET  /ui/api/noc/dashboard
POST /ui/api/noc/supervisor/tick
GET  /ui/api/noc/incidents
GET  /ui/api/noc/incidents/{id}
POST /ui/api/noc/incidents/{id}/recheck
POST /ui/api/noc/incidents/{id}/acknowledge
POST /ui/api/noc/incidents/{id}/resolve
```

A Central NOC apresenta a fila de incidentes, causa provável, flapping, investigação vinculada, ocorrências e linha do tempo. O estado persistido também contém a execução autônoma, resultado do watcher, comunicações, destino de escalonamento e playbook candidato quando existentes.

## Modo degradado

O Incident Manager é uma camada adicional, não um ponto único de falha. Se o estado Redis do Supervisor falhar durante a entrada do webhook, o fluxo de troubleshooting existente continua disponível e o erro é devolvido como diagnóstico da camada NOC.
