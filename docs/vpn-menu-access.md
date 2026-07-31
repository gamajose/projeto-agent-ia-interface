# Acesso automatizado pelo menu VPN

O ambiente operacional não usa o Monitor 1 como um bastion TCP convencional. O Agent abre um terminal SSH no Monitor 1 e reproduz o mesmo fluxo do operador:

1. Conecta em `SSH_SRV_VPN_IP` com `SSH_SRV_VPN_USER` e `SSH_SRV_VPN_SENHA`.
2. Executa `vpn <IP_DO_CLIENTE>`.
3. Lê o inventário exibido pelo terminal e procura a linha cujo `IP_VPN` é exatamente o alvo.
4. Envia o número da linha encontrada.
5. Confirma o acesso com `y`.
6. Autentica no servidor com `SSH_DEFAULT_PASSWORD`.
7. Em pfSense, usa as credenciais `SSH_FIREWALL_PF_*` e envia a opção `8` para abrir o shell.
8. Executa somente as ferramentas de leitura autorizadas pelo catálogo e pelas políticas.

## Variáveis

```dotenv
SSH_SRV_VPN_IP=10.17.181.1
SSH_SRV_VPN_PORT=22
SSH_SRV_VPN_USER=jose.moraes
SSH_SRV_VPN_SENHA=

SSH_ACCESS_MODE=vpn_menu
SSH_VPN_COMMAND=vpn {host}
SSH_VPN_MENU_TIMEOUT=45

SSH_DEFAULT_USER=2com
SSH_DEFAULT_PASSWORD=

SSH_FIREWALL_PF_USER=root
SSH_FIREWALL_PF_PASSWORD=
SSH_FIREWALL_PF_PORT=2224
SSH_FIREWALL_PF_SHELL_OPTION=8
```

`SSH_ACCESS_MODE` pode ser definido como `direct` somente em ambientes onde o servidor intermediário realmente oferece encaminhamento `direct-tcpip` para o alvo.

## Inventário e histórico

A coluna `NOME_CLIENTE` é extraída da mesma linha escolhida pelo IP. Parênteses usados apenas para apresentação são normalizados:

- `HOTBEL (MONITOR)` vira `HOTBEL MONITOR`.
- `ATACADAO CENTRAL (PF)` vira `ATACADAO CENTRAL PF`.

Depois de uma conexão bem-sucedida, esse nome passa a ser o nome exibido no inventário e também atualiza investigações anteriores que tenham o mesmo IP VPN. O hostname real coletado dentro do servidor continua disponível nas evidências da investigação.

## Segurança

- Nenhuma senha é incluída nos eventos de progresso, resultados ou logs da aplicação.
- A chave SSH do Monitor 1 continua sujeita à política `SSH_STRICT_HOST_KEY_CHECKING`.
- O alvo é selecionado por correspondência exata de IP, sem assumir que estará na linha 1.
- Produção e standby continuam bloqueados para alterações automáticas.
