# Autopilot seguro do Agent IA

O autopilot usa o mesmo motor de investigação, os mesmos playbooks, a mesma memória operacional e as mesmas políticas do Agent IA. Ele não cria um caminho paralelo e não amplia permissões.

## Fluxo automático

1. Recebe IP, hostname ou alvo conhecido e o objetivo operacional.
2. Consulta rapidamente os provedores configurados.
3. Segue a ordem de `AI_AUTO_PROVIDER_ORDER`.
4. Executa geração JSON real no primeiro candidato.
5. Se o candidato falhar, valida o próximo automaticamente.
6. Somente depois de selecionar uma IA saudável resolve inventário, porta e bastion.
7. Abre o SSH com validação de chave de host.
8. Descobre identidade e classifica o ambiente.
9. Seleciona automaticamente playbook e casos semelhantes.
10. Coleta evidências somente leitura, em múltiplas rodadas orientadas a hipóteses.
11. Produz causa provável, conclusão, recomendações, texto para ticket e ações seguras propostas.
12. Encerra o SSH e persiste a investigação no PostgreSQL.

## O que o autopilot nunca faz

- não executa correções durante a abertura;
- não reinicia ou desliga servidores;
- não acessa bancos de dados de clientes;
- não altera produção ou standby;
- não para, reinicia ou remove containers;
- não altera firewall, pacotes ou arquivos arbitrários;
- não transforma execução em fila em aprovação implícita.

Ações corretivas continuam dependendo de ambiente permitido, ferramenta autorizada pelo playbook, segunda IA, token assinado, aprovação humana e pós-validação.

## Configuração

```env
AGENT_AUTOPILOT_ENABLED=true
AGENT_AUTOPILOT_DEFAULT=true
AI_AUTO_PROVIDER_ORDER=groq,omniroute,gemini,ollama,openrouter
```

A ordem é configurável. Provedores não configurados ou indisponíveis são ignorados. O modelo selecionado pelo preflight é mantido durante toda a investigação.

## Interface

Na tela **Nova análise**, escolha **Automático — melhor IA disponível**. Essa opção é o padrão quando `AGENT_AUTOPILOT_DEFAULT=true`.

O operador continua informando somente:

- alvo;
- objetivo;
- ambiente, quando já conhecido;
- porta SSH, apenas quando precisa sobrescrever inventário e playbook.

O resultado inclui a IA efetivamente utilizada, o caminho de seleção, alvo resolvido, ambiente identificado, playbook, quantidade de evidências, proposta e estado da aprovação humana.
