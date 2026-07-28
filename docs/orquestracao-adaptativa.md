# Orquestração adaptativa de ferramentas

## Objetivo

O Agent IA não deve executar uma sequência fixa de comandos para todos os alertas. A investigação passa a ser construída com base no que existe no alvo, no problema informado, nas evidências já coletadas e no resultado de tentativas anteriores.

## Descoberta inicial

Após o SSH e antes do planejamento, o Agent executa uma coleta somente leitura que identifica dinamicamente:

- sistema operacional, kernel e init;
- executáveis realmente presentes;
- serviços e unidades existentes;
- listeners TCP e UDP;
- Docker ou Podman e seus containers;
- filesystems montados.

O resultado forma o `runtime_context`. Nenhuma versão, serviço, hostname, container ou ferramenta é presumido como verdade antes dessa coleta.

## Catálogo composto

O planejador recebe dois grupos de ferramentas:

1. ferramentas estruturadas já existentes para Linux, Checkmk, systemd, rede e filesystem;
2. ferramentas adaptativas genéricas:
   - `runtime.snapshot`;
   - `service.search`;
   - `process.search`;
   - `logs.search`;
   - `network.listeners`;
   - `network.resolve`;
   - `network.path`;
   - `container.inventory`;
   - `container.logs`;
   - `package.search`;
   - `file.search`.

Todas são somente leitura. Argumentos são validados e escapados antes da execução.

## Seleção dinâmica

A cada rodada, o Agent recalcula uma lista de ferramentas recomendadas usando:

- palavras do objetivo;
- capacidades e tecnologias detectadas no alvo;
- serviços, containers e executáveis observados;
- ferramentas usadas em investigações semelhantes;
- ferramentas que já falharam, foram bloqueadas ou não existem;
- ferramentas já executadas na investigação atual.

A recomendação é um apoio ao planejador, não um roteiro rígido. A IA pode escolher outra ferramenta do catálogo quando a evidência justificar.

## Adaptação após falhas

Cada resultado de ferramenta é classificado como executado, falhou, indisponível ou bloqueado. Quando ocorre falha, o Agent inclui alternativas da mesma categoria que estejam disponíveis no alvo. Na rodada seguinte, o planejador recebe o feedback e muda a estratégia.

Exemplos:

- `tracepath` ausente: o catálogo pode usar `traceroute` ou `ping` por meio de `network.path`;
- nome exato do serviço desconhecido: `service.search` descobre a unidade antes de `systemd.inspect_unit`;
- container runtime desconhecido: `container.inventory` detecta Docker ou Podman;
- erro sem unidade conhecida: `logs.search` procura a mensagem literal nos logs recentes;
- pacote ou binário inesperado: `package.search` e o snapshot mostram o que realmente está instalado.

## Memória operacional

Investigações semelhantes continuam sendo consultadas no PostgreSQL. Ferramentas recorrentes no histórico recebem reforço na recomendação, mas o conteúdo antigo nunca substitui a validação atual do alvo.

## Segurança

A orquestração adaptativa não altera as políticas existentes:

- produção e standby recebem somente investigação e proposta;
- reboot e shutdown permanecem proibidos;
- nenhum banco de cliente é acessado;
- nenhuma ferramenta adaptativa reinicia, para ou remove containers;
- correções continuam restritas ao playbook, segunda IA, token, aprovação humana e pós-validação.

## Configuração

```env
AGENT_RUNTIME_DISCOVERY_ENABLED=true
AGENT_ADAPTIVE_TOOLS_ENABLED=true
AGENT_TOOL_RECOMMENDATION_LIMIT=10
```

`AGENT_TOOL_RECOMMENDATION_LIMIT` limita apenas as sugestões priorizadas enviadas ao planejador. O catálogo completo de ferramentas disponíveis continua sendo fornecido para que a investigação possa mudar de direção.
