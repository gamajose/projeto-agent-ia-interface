# Interface web operacional do Agent IA

A interface adiciona uma camada visual ao mesmo motor AIOps já usado pelo comando `agent`. Ela não cria um segundo banco, não replica playbooks e não substitui as políticas de segurança.

## O que a interface entrega

- painel com métricas e investigações recentes do PostgreSQL existente;
- formulário para informar IP, hostname ou site salvo no inventário;
- seleção de ambiente, modo de análise e porta SSH opcional;
- atalhos para ocorrências comuns de Checkmk, disco, memória, SNMP e containers;
- histórico pesquisável de investigações;
- inventário aprendido automaticamente depois de uma descoberta SSH bem-sucedida;
- catálogo dos playbooks YAML já instalados;
- criação revisada de playbooks pela própria interface, usando somente ferramentas estruturadas de leitura;
- oferta de rascunho quando uma investigação termina sem playbook correspondente;
- acompanhamento persistente da etapa atual mesmo depois de fechar o painel de resultado;
- resultado com causa provável, confiança, fatos, recomendações e texto para ticket;
- aprovação humana separada quando uma correção segura foi proposta e aprovada pela segunda IA.

## Arquitetura

A interface é uma SPA em HTML, CSS e JavaScript puro, servida pelo mesmo FastAPI. Não há Node.js, npm ou etapa de build no servidor.

```text
Navegador
   |
   | /ui e /ui/api/*
   v
FastAPI (app.web_main)
   |
   +-- serviços existentes do Agent IA
   +-- playbooks em config/playbooks
   +-- PostgreSQL existente
   +-- Redis existente, quando AGENT_EXECUTION_MODE=queue
   +-- SSH/bastion e provedores de IA já configurados
```

A API administrativa continua protegida por `AGENT_API_TOKEN`. Esse token não é enviado ao navegador. A interface chama funções internas do servidor e restringe acesso por rede confiável.

## Configuração

Adicione ao `.env`:

```env
# Perfil exibido como já conectado na interface.
AGENT_UI_OPERATOR_NAME=José Luiz

# Escute em todas as interfaces somente quando o acesso estiver protegido por VPN,
# firewall ou proxy reverso autenticado.
AGENT_UI_HOST=0.0.0.0
AGENT_UI_PORT=8080
AGENT_UI_ENABLED=true

# Informe as redes autorizadas, separadas por vírgula.
# Inclua 127.0.0.1/32 quando usar Nginx local como proxy reverso.
AGENT_UI_ALLOWED_NETWORKS=127.0.0.1/32,172.27.232.0/24
```

O perfil é carregado automaticamente porque a interface foi pensada para uma rede operacional confiável. Não publique a porta diretamente na internet. Para acesso externo ou múltiplos usuários, use autenticação no proxy reverso ou integre OIDC/SSO em uma etapa posterior.

## Execução

Instale o projeto como já é feito atualmente:

```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Inicie a interface:

```bash
agent-web
```

Ou diretamente pelo Uvicorn:

```bash
uvicorn app.web_main:app --host 0.0.0.0 --port 8080
```

Acesse:

```text
http://IP_DO_SERVIDOR:8080/ui
```

## Acompanhamento da investigação

A interface inicia a investigação em uma execução acompanhável e devolve imediatamente um identificador. O backend registra as etapas reais de validação da IA, resolução do alvo, SSH, coleta/análise, persistência e conclusão.

Fechar o drawer não interrompe a investigação. Um cartão fixo permanece visível no navegador e permite voltar ao andamento ou abrir o resultado final. O identificador ativo fica no `localStorage`; os detalhes permanecem no processo web por até 24 horas. Reiniciar o serviço web remove apenas esse acompanhamento temporário, não o histórico já persistido no PostgreSQL.

## Playbooks

O catálogo de playbooks não é armazenado no banco. A fonte de verdade continua sendo arquivos YAML no diretório definido por `AGENT_PLAYBOOK_DIR`, cujo padrão é:

```text
config/playbooks
```

O PostgreSQL armazena o histórico de investigações, o playbook utilizado e os dados de efetividade que ajudam na seleção futura.

O botão **Adicionar playbook** cria um novo YAML após validação. Pela interface:

- somente ferramentas estruturadas conhecidas são aceitas;
- comandos shell são recusados;
- ferramentas corretivas são recusadas;
- o operador revisa identificador, título, perfis, padrões e etapas antes de salvar;
- depois do salvamento, o cache é recarregado e o playbook entra no catálogo.

Quando uma investigação não utilizou um playbook, o resultado oferece **Criar rascunho de playbook**. O rascunho reaproveita apenas ferramentas de leitura realmente planejadas e exige revisão humana antes de ser gravado.

## Inventário aprendido

Depois que o SSH foi autenticado e a descoberta do host retornou identidade, sistema operacional e endereços, o Agent atualiza a tabela `hosts` usando o endereço resolvido e a porta utilizada. Uma falha isolada ao atualizar o inventário é mostrada no resultado, mas não descarta a investigação já concluída.

## Exemplo de serviço systemd

```ini
[Unit]
Description=Agent IA Infra - Interface Web
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=jose
WorkingDirectory=/opt/agent-ia
EnvironmentFile=/opt/agent-ia/.env
ExecStart=/opt/agent-ia/.venv/bin/agent-web
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Depois:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-ia-web.service
sudo systemctl status agent-ia-web.service --no-pager -l
```

## Fluxo de correção

1. O operador abre uma investigação em `propose`.
2. O agente coleta evidências em modo de leitura.
3. O playbook limita quais ferramentas corretivas podem ser propostas.
4. A segunda IA revisa causa, impacto e pós-validação.
5. O backend gera um token temporário ligado ao UUID e ao conteúdo exato das ações.
6. A interface exibe o botão de aprovação somente nessa sessão.
7. Após a aprovação, o backend valida novamente o token, o ambiente e as políticas antes de executar.

Produção, standby e ambiente desconhecido continuam sem alteração automática. Reboot, shutdown, acesso a banco de cliente e ciclo de vida de containers continuam bloqueados.

## Banco de dados

Nenhuma migration nova é necessária. A interface usa as tabelas já existentes:

- `investigations` para histórico, evidências e conclusões;
- `hosts` para alvos aprendidos;
- `monitoring_mappings` para site, container e host Checkmk;
- `approval_executions` para auditoria das aprovações.
