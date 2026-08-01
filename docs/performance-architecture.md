# Arquitetura de performance

Este documento descreve o pacote experimental da versão 1.26.0. A branch permanece fora da `main` enquanto a interface e o comportamento são avaliados no WSL. A abertura da pull request serve apenas para CI; nenhum merge ou deployment faz parte desta etapa.

## Execuções

- O estado curto de cada execução usa Redis quando disponível.
- Redis Streams transporta os eventos consumidos por Server-Sent Events.
- A ausência do Redis não impede a interface: o armazenamento volta para memória.
- O navegador tenta SSE primeiro e usa polling como fallback.

## Orçamento

Cada investigação possui limites globais de comandos, chamadas de IA, tempo total, tempo por host e quantidade de hosts aprofundados. Atingir um limite interrompe somente a coleta em andamento e não reinicia servidores.

## Multi-host

Os hosts relacionados passam por uma triagem determinística. Somente os hosts com indícios relevantes são aprofundados pela IA, respeitando o orçamento global. O salto SSH interno utiliza um ControlMaster temporário no servidor de entrada e remove o socket ao terminar.

## Cache

Somente dados de baixa volatilidade recebem cache curto, como topologia e capacidades. Estado de serviços, alertas e uso de recursos continuam sendo coletados durante a investigação.

## Métricas

O endpoint `/metrics` expõe duração das investigações, conexões SSH, comandos, chamadas de IA, falhas e limites atingidos. O endpoint `/ui/api/observability` fornece um resumo para a interface.

## Segurança

- Produção e standby permanecem somente leitura.
- Investigações multi-host não geram autorização corretiva.
- Clientes diferentes não compartilham rotas.
- Acesso a bancos de dados de clientes continua bloqueado.
- Cancelamento encerra somente o comando atual e a investigação associada.
