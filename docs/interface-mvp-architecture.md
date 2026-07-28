# Arquitetura da interface operacional

## Princípio

A interface web não possui um motor próprio de investigação. Ela chama os mesmos serviços usados pelo CLI, webhook e worker:

```text
Interface / CLI / Webhook
          |
          v
       run_target
          |
          +-- valida provedor antes do SSH
          +-- resolve alvo, inventário e porta
          +-- aplica seleção de playbook por contexto
          +-- cria SSHExecutor, inclusive bastion/VPN
          v
run_dynamic_investigation
          |
          +-- descoberta e classificação do ambiente
          +-- memória PostgreSQL e casos semelhantes
          +-- playbook YAML e ferramentas estruturadas
          +-- planejamento e análise por rodadas
          +-- conclusão, ticket, revisão e aprovação
          v
       PostgreSQL
```

Nenhuma regra operacional foi movida para JavaScript. O frontend apenas coleta escolhas, apresenta estados e consome a API.

## Componentes reutilizados

- `app.services.runner`: entrada única para alvo, SSH, provedor e playbook;
- `app.services.dynamic_agent`: planejamento, coleta, análise e persistência;
- `app.services.ai_providers`: Gemini, Groq, OpenRouter, Ollama e OmniRoute;
- `app.services.provider_preflight`: valida credencial, endpoint, modelo e rota antes do SSH;
- `app.services.playbooks`: seleção automática, manual ou sem playbook;
- `app.services.jobs`: serialização da execução para Redis/worker;
- `app.services.persistence`: investigações, evidências e memória operacional;
- `app.core.policies` e `app.services.tool_registry`: bloqueios e pós-validação.

## Seleção de IA

A escolha feita na interface é aplicada com `ContextVar` somente durante a investigação. Isso evita mudar `AI_PROVIDER` globalmente e permite jobs concorrentes usando provedores diferentes.

A API nunca retorna tokens. O endpoint de provedores retorna somente:

- nome e rótulo;
- estado do diagnóstico;
- modelo ou rota;
- latência;
- motivo seguro da indisponibilidade;
- indicador de seleção permitida.

Provedores opcionais sem credencial aparecem como `not_configured` e não derrubam a aplicação.

## Seleção de playbook

A interface expõe os modos já existentes no backend:

- `auto`: YAML + memória operacional confiável;
- `manual`: ID escolhido pelo operador e validado no catálogo;
- `none`: sem coleta inicial de playbook, mantendo catálogo de ferramentas e políticas.

O banco nunca cria ou altera arquivos YAML.

## Modo corrigir

A abertura da investigação nunca recebe `approve=true`. Na interface, “corrigir” significa investigar, preparar uma proposta e seguir para a aprovação humana separada. O backend normaliza essa abertura para proposta, preservando:

- ambiente permitido;
- segunda IA revisora;
- token assinado e temporário;
- aprovação explícita;
- execução por ferramenta estruturada;
- pós-validação funcional.

## Painel de saúde

O painel consulta:

- PostgreSQL com `SELECT 1`;
- Redis e profundidade da fila;
- modo inline ou worker;
- diretório e quantidade de playbooks;
- todos os provedores por preflight;
- versão, branch e commit locais.

Segredos, DSNs, tokens, senhas e chaves não são retornados.

## Segurança preservada

Continuam no backend e não podem ser contornadas pela UI:

- acesso a banco de cliente bloqueado;
- reboot, shutdown e parada do host bloqueados;
- ciclo de vida de containers bloqueado;
- produção e standby sem correção automática;
- desconhecido somente leitura;
- correção limitada a monitoring e training;
- aprovação, segunda IA e pós-validação obrigatórias;
- redação de credenciais em jobs, evidências e resultados.

## Etapas seguintes

### Etapa 2 — acompanhamento detalhado

- eventos estruturados por rodada no Redis;
- polling incremental ou SSE;
- status da conexão bastion e destino;
- hipótese, ferramenta, finalidade e confiança parcial;
- cancelamento cooperativo entre rodadas, nunca durante uma ação corretiva.

### Etapa 3 — histórico e feedback

- filtros adicionais por perfil, categoria, componente, playbook e data;
- feedback resolvido, não resolvido, verificado ou inconclusivo;
- exportação JSON/PDF e texto de ticket;
- métricas de efetividade por playbook e ferramenta.

### Etapa 4 — segurança SSH interativa

- endpoint de inspeção de fingerprint sem conexão operacional;
- confirmação do operador para registrar chave no `known_hosts`;
- trilha de auditoria da aprovação;
- nunca ativar política permissiva silenciosamente.

### Etapa 5 — autenticação e RBAC

- provedor de identidade externo ou proxy autenticado;
- perfis N1, N2 e N3;
- autorização das ações no backend;
- sessão local atual mantida apenas para ambiente confiável e restrito por rede.
