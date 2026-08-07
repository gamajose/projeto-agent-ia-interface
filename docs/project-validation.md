# Validação assistida de projetos

A seção **Projetos** parte do princípio de que o operador não deve preencher informações que a própria ferramenta consegue descobrir no ambiente.

## Entrada mínima

Para o alvo principal, a interface pede o **tipo de validação** e o **IP VPN/TAP**. Em Produção/Standby também é informado apenas o papel do servidor. O sistema acessa o IP pelo fluxo SSH/VPN já configurado e descobre automaticamente:

- sistema operacional e versão;
- IP interno e rotas;
- máquina física ou virtual;
- fabricante e modelo do equipamento;
- existência e tipo de interface iDRAC, iLO, ILOM ou xClarity;
- IP da interface de gerenciamento quando `ipmitool` o informa;
- listener/agente Checkmk na porta 6556;
- data e sincronismo de horário.

Cliente, ticket, nome do host, SO, IP interno e tipo/IP da interface de gerenciamento não são campos do formulário.

## Infraestrutura 2Com

A tela não pede dados que já pertencem à configuração da aplicação. O fluxo reutiliza o `.env`/Vault existente:

```dotenv
SSH_SRV_VPN_IP=
SSH_SRV_VPN_PORT=22
SSH_SRV_VPN_USER=
SSH_SRV_VPN_SENHA=
SSH_CMK05=
API_WHATSAPP=
```

O Monitor 1 usa a conexão já configurada como bastion. Para o CMK05, o projeto usa o endereço de `SSH_CMK05`; a credencial operacional segue a mesma configuração já utilizada pelo fluxo do Monitor 1. A API do WhatsApp é lida de `API_WHATSAPP`.

## Servidor de monitoramento do cliente

Quando um projeto de Produção/Standby, Interface de Gerenciamento ou Windows possui monitor dedicado/compartilhado, o operador marca **Tem servidor de monitoramento do cliente** e informa somente o **IP VPN/TAP desse monitor**. A ferramenta acessa o monitor e descobre o IP interno por conta própria.

No cenário **Servidor Linux — Monitoramento**, os demais servidores podem ser informados somente pelos IPs VPN/TAP, por exemplo:

```text
production | 172.27.232.210
standby | 172.27.232.211
```

A ferramenta acessa cada IP VPN, identifica o IP interno correspondente e então monta os testes `nc` entre monitor, produção e standby usando os **IPs internos**, nunca os IPs VPN para a comunicação local do cliente.

## Interface de gerenciamento

O operador não escolhe iDRAC/iLO/ILOM/xClarity e não informa previamente o IP da controladora. A ferramenta coleta `dmidecode -t1` e `ipmitool lan print`, correlaciona fabricante/modelo e registra o que encontrou. Se existir servidor de monitoramento compartilhado, o teste SNMP pode ser executado a partir dele.

As credenciais SNMP continuam fora do código e devem permanecer no `.env`/Vault:

```dotenv
SNMP_V2_COMMUNITY=
SNMP_V3_USER=doiscom
SNMP_V3_AUTH_PASSWORD=
```

## Cenários

A função mantém os fluxos de Linux Produção/Standby, Linux Monitoramento, Interface de Gerenciamento, Firewall, Windows e Resolução DNS da VPN. Windows continua usando o fluxo RDP/Socat quando necessário. No DNS, a versão do Linux é descoberta antes de apresentar o procedimento compatível com OL7 ou OL8/9.

## Segurança

A descoberta automática é somente leitura. Instalação de pacotes/agente, criação de Socat, abertura persistente de listeners, alteração de DNS, reinício de rede/VPN e demais mudanças continuam separadas como etapas manuais ou sujeitas a aprovação. Produção e standby permanecem sem correção automática.
