# Ensemble e varredura operacional

A partir da versão 1.32.0, a investigação pode receber um host individual ou uma faixa IPv4 privada.

## Faixas aceitas

Exemplos:

```text
172.27.232.0/24
172.27.232.10-172.27.232.80
172.27.232.10-80
```

A varredura é executada a partir do Monitor 1 configurado em `SSH_SRV_VPN_IP`. O Monitor 1 testa somente as portas SSH configuradas em `AGENT_RANGE_SCAN_PORTS`. Endereços públicos são bloqueados por padrão.

O fluxo é:

1. expandir e validar a faixa;
2. acessar o Monitor 1 uma única vez para localizar endpoints SSH;
3. autenticar e executar triagem de leitura em todos os endpoints encontrados;
4. em faixas pequenas, aprofundar todos os hosts autenticados;
5. em faixas maiores, aprofundar os hosts com sinais anormais até o limite configurado;
6. correlacionar os resultados e apontar o host com a causa mais sustentada;
7. gerar proposta corretiva somente quando um playbook permitido casar com as evidências;
8. exigir revisão por IA e aprovação humana antes de qualquer correção autorizada.

Se o operador não informar um objetivo, a missão passa a ser uma análise geral de saúde, cobrindo sistema, recursos, rede, containers e monitoramento.

## Ensemble de IA

O coordenador consulta até `AGENT_ENSEMBLE_SIZE` provedores disponíveis. Planejamento, análise e conclusão são agregados; a correção usa votação estrita das ações propostas. O crítico final continua separado para não perder independência.

O Ensemble de playbooks ranqueia múltiplos playbooks pelo texto do objetivo, perfil do host e efetividade histórica. Os playbooks servem de fontes consultivas. Para correção, o escopo não é unido indiscriminadamente: permanece o playbook primário ou um playbook que casar posteriormente com a causa observada.

## Limites

```dotenv
AGENT_ENSEMBLE_ENABLED=true
AGENT_ENSEMBLE_SIZE=3
AGENT_ENSEMBLE_MIN_SUCCESS=2
AGENT_PLAYBOOK_ENSEMBLE_LIMIT=4

AGENT_RANGE_PRIVATE_ONLY=true
AGENT_RANGE_SCAN_PORTS=22,2224
AGENT_RANGE_MAX_ADDRESSES=512
AGENT_RANGE_MAX_DISCOVERED_HOSTS=128
AGENT_RANGE_SCAN_CONCURRENCY=32
AGENT_RANGE_TRIAGE_TIMEOUT_SECONDS=35
AGENT_RANGE_FULL_ANALYSIS_THRESHOLD=12
AGENT_RANGE_DEEP_DIVE_LIMIT=16
```

O limite de hosts descobertos não é truncado silenciosamente: se mais hosts responderem que o permitido, a execução falha e exige ajuste explícito do limite.

## Aprovação

A varredura nunca aplica correção durante a descoberta. O resultado consolidado aponta o host raiz. Se esse host estiver em um ambiente elegível para correção e houver ações aprovadas pelo playbook e pela IA revisora, a interface apresenta o botão de aprovação existente. Produção e standby continuam protegidos pelas políticas atuais.
