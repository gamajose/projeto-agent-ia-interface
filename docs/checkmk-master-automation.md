# NOC autônomo orientado pelo Checkmk master

## Papel do CMK05

O NOC usa o `CMK05` como fonte primária de inventário e de estados. A configuração padrão espera o container `checkmk-master-25` e o site `master`.

O `sites.mk` do master é lido somente para obter uma lista segura de sites remotos. O parser exporta apenas:

- `site_id`;
- alias do cliente;
- ativo/desativado;
- host e porta Livestatus;
- `status_host`/`status_site`;
- modo de replicação;
- URL do site quando presente.

O campo `secret` e outros dados de autenticação não são exportados, persistidos ou enviados à interface.

## Fluxo principal

```text
CMK05 / master
    -> sites ativos
    -> Livestatus de cada site
    -> hosts + IPs internos
    -> somente WARN/CRIT/UNKNOWN/DOWN
    -> Incident Manager
    -> Skill Router
    -> contexto do cliente
    -> investigação
    -> Supervisor / política
```

Estados `OK` não geram investigação de IA. A aplicação usa o Checkmk como camada de sensores e trabalha apenas sobre anomalias.

## Isolamento por cliente

`site_id` é a fronteira de segurança do inventário.

Um endereço interno nunca é resolvido globalmente. O alvo é sempre identificado por `site_id + host_name`, e o IP interno só é válido dentro desse site. Assim, dois clientes podem usar `192.168.1.15` sem que a aplicação misture os ambientes.

Para sites com entrada dedicada, o caminho automático é:

```text
Monitor 1
  -> vpn <entrada do site>
  -> servidor de entrada do cliente
  -> SSH para o IP interno do mesmo site
```

O segundo salto reutiliza a sessão do cliente por meio de `NestedSSHExecutor`.

### Endpoints compartilhados

Quando vários sites usam o mesmo host Livestatus, por exemplo um nó concentrador com portas diferentes, o site é marcado como `shared_endpoint`.

Até existir uma rota de acesso interna específica e comprovada para esse site, o NOC pode registrar o incidente, mas não abre investigação automática a partir daquele endpoint. Isso evita usar o namespace/sessão de um cliente no ambiente de outro.

## Alvos especiais

- `checkmk-<site>` com `0.0.0.0`: investigar no servidor de entrada/monitoramento do cliente; nunca executar `ssh 0.0.0.0`.
- iDRAC/ILOM/BMC: investigar transporte SNMP/BMC a partir do contexto do cliente; não presumir SSH no equipamento de gerenciamento.
- firewall/rede: executar diagnóstico a partir do contexto do cliente; não alterar rota, VPN, gateway ou firewall automaticamente.
- servidor Linux comum: quando permitido e houver IP interno válido, usar o segundo salto SSH dentro do mesmo site.

## Skills

As skills ficam em `config/skills/*.yml`. Elas transformam conhecimento operacional em regras reutilizáveis de investigação.

Uma skill define:

- padrões de host, serviço e output Checkmk;
- prioridade;
- estratégia de alvo (`internal_ssh` ou `entry_context`);
- playbook relacionado quando aplicável;
- conhecimento operacional;
- restrições de segurança.

Skills iniciais:

- pressão de memória/swap Linux;
- filesystem Linux;
- runtime Checkmk;
- SNMP/BMC;
- link/gateway/firewall;
- Oracle/backup.

O alerta continua sendo tratado como sintoma. A skill direciona a coleta; ela não transforma o texto do Checkmk em causa raiz automaticamente.

## Ciclos

Por padrão:

- leitura de anomalias do master: a cada 120 segundos;
- sincronização completa de sites/hosts: a cada 6 horas;
- descoberta de rede `172.27.*`: somente manual/contingência.

A sincronização pode ser forçada pela interface em **Sincronizar Checkmk** e a leitura de estados em **Ronda agora**.

## Variáveis

```env
CHECKMK_MASTER_ENABLED=true
CHECKMK_MASTER_TARGET=10.17.181.44
CHECKMK_MASTER_SSH_PORT=22
CHECKMK_MASTER_SSH_USER=<conta autorizada no CMK05>
CHECKMK_MASTER_CONTAINER=checkmk-master-25
CHECKMK_MASTER_SITE=master
CHECKMK_MASTER_COMMAND_TIMEOUT_SECONDS=120
CHECKMK_MASTER_SOCKET_TIMEOUT_SECONDS=10
CHECKMK_MASTER_CONCURRENCY=16
CHECKMK_MASTER_MAX_SITES=1000
CHECKMK_MASTER_MAX_RECORDS=50000

CHECKMK_MASTER_PATROL_ENABLED=true
CHECKMK_MASTER_POLL_INTERVAL_SECONDS=120
CHECKMK_MASTER_INVENTORY_SYNC_HOURS=6
```

`CHECKMK_MASTER_SSH_PASSWORD` é opcional. Quando não configurada, a aplicação reutiliza o segredo SSH padrão já existente. Senhas nunca devem ser versionadas.

## Descoberta de rede

A descoberta de rede existente não foi removida. Ela passa a ser um mecanismo manual de contingência para divergência de inventário, ambiente ainda não cadastrado ou validação excepcional.

Uma descoberta que já estava em andamento continua do cursor salvo mesmo após atualização/restart. O novo desenho apenas impede que a ronda por SSH seja o sensor principal do NOC.
