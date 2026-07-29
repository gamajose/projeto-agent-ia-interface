# Instalação unificada e portátil

A instalação padronizada usa sempre a mesma estrutura, independentemente de o host ser Linux físico, VM ou WSL com systemd:

```text
/opt/agent-ia/
├── app/       clone Git do projeto e arquivo .env
├── venv/      ambiente virtual Python
├── config/    configuração protegida do OmniRoute
└── data/      metadados persistentes do Agent
```

O caminho pode ser alterado por `AGENT_INSTALL_ROOT`, mas manter `/opt/agent-ia` facilita suporte, atualização e documentação entre máquinas.

## Instalação em uma máquina nova

Execute como usuário com acesso a `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/gamajose/projeto-agent-ia-interface/main/install.sh | bash
```

O instalador solicita a senha do `sudo` diretamente ao sistema. Essa senha **não é gravada** em arquivo algum.

Durante a instalação interativa, ele pode solicitar:

- nome exibido do operador;
- usuário SSH padrão dos alvos;
- senha SSH opcional, armazenada somente no `.env` com modo `600`;
- dados opcionais do bastion/servidor de VPN;
- senha inicial do painel OmniRoute, ou geração automática de uma senha forte.

Para automação sem perguntas:

```bash
curl -fsSL https://raw.githubusercontent.com/gamajose/projeto-agent-ia-interface/main/install.sh \
  | bash -s -- --non-interactive
```

## O que o comando prepara

1. instala `git`, `curl`, Python, `venv` e certificados do sistema;
2. instala Docker pelo instalador oficial quando o comando ainda não existe;
3. valida Docker Compose v2;
4. baixa ou atualiza o projeto em `/opt/agent-ia/app`;
5. cria o Python em `/opt/agent-ia/venv`;
6. gera ou preserva `.env` e segredos locais;
7. inicia PostgreSQL e Redis em loopback;
8. inicia o OmniRoute em container com volume persistente;
9. inicializa o schema do Agent no PostgreSQL;
10. cria e ativa os serviços systemd;
11. valida containers e interface web.

## Serviços criados

```text
agent-ia-infra.service   PostgreSQL e Redis
omniroute.service        gateway OmniRoute
agent-ia-web.service     aplicação e interface web
```

Validação:

```bash
sudo systemctl status agent-ia-infra omniroute agent-ia-web --no-pager -l
sudo /opt/agent-ia/app/scripts/stack_control.sh status all
```

Logs:

```bash
sudo journalctl -u agent-ia-web -f
sudo docker logs omniroute --tail 100
```

## Persistência

O PostgreSQL e o Redis usam volumes Docker próprios. O OmniRoute atual usa SQLite no volume `omniroute_data`; por isso não é criado um segundo PostgreSQL para ele.

```text
postgres_data    histórico, inventário e aprovações do Agent
redis_data       fila e resultados temporários quando o modo queue é usado
omniroute_data   banco SQLite, provedores e configuração do OmniRoute
```

Os containers ficam publicados apenas em loopback por padrão:

```text
127.0.0.1:5432   PostgreSQL
127.0.0.1:6379   Redis
127.0.0.1:20128  OmniRoute
```

A interface do Agent escuta em `0.0.0.0:8080`, mas aceita somente as redes locais detectadas e registradas em `AGENT_UI_ALLOWED_NETWORKS`.

## Reutilização de instalações existentes

O instalador é idempotente:

- preserva `.env` existente;
- preserva senhas já configuradas;
- atualiza somente caminhos gerenciados pela instalação;
- reutiliza containers com os nomes `agent-ia-postgres`, `agent-ia-redis` e `omniroute`;
- não remove volumes;
- não usa `docker compose down`;
- não força checkout quando existem alterações locais no clone instalado.

Quando um container antigo é reutilizado, o `.env` precisa conter as credenciais compatíveis com ele. Uma falha de autenticação é exibida antes da inicialização do banco.

## Configuração do OmniRoute após a instalação

O serviço e o banco local ficam prontos, mas o instalador não cria contas ou chaves em provedores externos.

No painel `http://127.0.0.1:20128`:

1. conecte somente provedores autorizados;
2. crie um endpoint;
3. copie o token do endpoint;
4. abra **Configurações → OmniRoute** no Agent;
5. salve o token e as rotas publicadas;
6. execute o teste do provedor.

O arquivo protegido do gateway fica em:

```text
/opt/agent-ia/config/omniroute.env
```

## Atualização

O mesmo comando pode ser executado novamente:

```bash
curl -fsSL https://raw.githubusercontent.com/gamajose/projeto-agent-ia-interface/main/install.sh | bash
```

Ele atualiza o clone por fast-forward, reinstala as dependências Python e mantém configurações, containers e volumes.

Também é possível atualizar a partir do clone:

```bash
cd /opt/agent-ia/app
bash install.sh
```

## Opções

```text
--install-dir CAMINHO
--ref BRANCH_OU_TAG
--repo URL
--non-interactive
--skip-docker
--with-opencode
--without-opencode
```

Exemplo em outro caminho:

```bash
AGENT_INSTALL_ROOT=/srv/agent-ia \
  bash install.sh --install-dir /srv/agent-ia
```

## WSL

O WSL precisa estar com systemd ativo. O instalador não reinicia nem encerra o WSL automaticamente.

Quando systemd não estiver ativo, configure manualmente:

```ini
# /etc/wsl.conf
[boot]
systemd=true
```

Depois encerre o WSL pelo Windows e execute novamente o instalador.

O Docker pode ser o daemon nativo instalado no WSL ou o Docker Desktop com integração habilitada. O instalador preserva uma instalação Docker que já responda normalmente.

## Segurança

- senha do `sudo` nunca é salva;
- `.env` e `omniroute.env` usam modo `600`;
- diretórios de configuração usam modo `700`;
- bancos e gateway ficam em loopback por padrão;
- segredos existentes não são impressos no resumo;
- nenhum reboot ou shutdown é executado;
- o instalador não altera regras operacionais do Agent;
- produção e standby continuam somente com investigação e proposta.
