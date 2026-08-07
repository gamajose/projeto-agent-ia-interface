# Validação assistida de projetos

A seção **Projetos** monta um checklist conforme o cenário, o sistema operacional e os endereços informados pelo operador.

## Cenários

- Linux Produção/Standby
- Linux Monitoramento
- Interface de gerenciamento
- Firewall
- Windows
- Resolução DNS da VPN

## Separação de responsabilidades

O plano diferencia:

- coletas de leitura que podem ser enfileiradas para a IA;
- comandos assistidos que dependem do terminal correto;
- etapas manuais, como painel de indisponibilidade, bots e prints;
- alterações que exigem revisão, como instalação de pacotes/agente, Socat, DNS, listeners e reinícios.

Produção e standby continuam sem correção automática. As senhas permanecem no `.env`/Vault e não aparecem no checklist.

## Servidor de monitoramento do cliente

Ao marcar **Tem servidor de monitoramento do cliente**, a interface solicita nome, IP VPN e IP interno. O plano usa o IP interno nos testes entre monitor, produção e standby. O IP VPN é utilizado somente para abrir a sessão remota necessária à coleta.

No cenário de servidor de monitoramento, podem ser informados outros hosts no formato:

```text
Produção | production | 192.168.1.10 | 172.27.232.210
Standby | standby | 192.168.1.11 |
```

## Credenciais SNMP

As credenciais de iDRAC/ILOM não ficam no código ou nos playbooks. Configure no `.env` ou no backend de segredos:

```dotenv
SNMP_V2_COMMUNITY=
SNMP_V3_USER=doiscom
SNMP_V3_AUTH_PASSWORD=
```

Quando os valores não estiverem configurados, o plano mostra placeholders explícitos e mantém o `snmpwalk` como etapa assistida, sem execução automática.

## DNS da VPN

O playbook de DNS compara os resolvers informados, coleta `/etc/resolv.conf`, correlaciona os logs da VPN e apresenta os ajustes de OL7/OL8 como passos manuais. Ele não altera DNS, rede ou serviço OpenVPN automaticamente.

A porta do Monitor 5, os endereços dos monitores e os hosts relacionados são campos editáveis porque podem variar conforme o projeto.
