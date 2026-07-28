# Raciocínio inteligente do Agent IA

## O que caracteriza uso real de IA neste projeto

O Agent não usa o modelo somente para escrever uma resposta ao final. A IA participa das decisões operacionais durante toda a investigação, sempre limitada pelas políticas do backend.

O ciclo implementado é:

```text
entender → planejar → executar ferramenta → observar → refletir → replanejar → criticar
```

A conexão SSH e as ferramentas continuam determinísticas. A IA escolhe entre ferramentas permitidas, interpreta o retorno e decide qual lacuna precisa ser preenchida em seguida.

## 1. Interpretação da missão

Antes do planejamento técnico, a descrição livre do operador é convertida em:

- missão verificável;
- fatos conhecidos;
- lacunas;
- domínios candidatos;
- critérios de sucesso;
- restrições;
- condições de encerramento.

A causa não pode ser presumida nessa etapa.

## 2. Playbook consultivo

Com `AGENT_PLAYBOOK_ADVISORY_ONLY=true`, o playbook não dispara uma sequência fixa de comandos. Ele fornece ao planejador:

- contexto operacional conhecido;
- ferramentas permitidas;
- padrões históricos;
- correções que poderiam ser propostas posteriormente.

A IA decide se cada orientação é aplicável ao alvo atual. Toda conclusão precisa ser validada novamente no ambiente.

## 3. Planejamento adaptativo

Em cada rodada, o planejador recebe:

- missão e critérios de sucesso;
- sistema, serviços, listeners, containers e executáveis descobertos;
- catálogo real de ferramentas disponíveis;
- histórico semelhante;
- evidências já coletadas;
- ferramentas que falharam ou não existem;
- hipóteses confirmadas, descartadas e pendentes.

O plano deve selecionar ferramentas estruturadas e cada chamada precisa testar uma hipótese ou preencher uma lacuna.

## 4. Execução e observação

A IA não executa shell arbitrário quando existe ferramenta estruturada. Os argumentos são validados e escapados no backend.

Depois de cada chamada, o retorno contém:

- código de saída;
- stdout e stderr sanitizados;
- dados normalizados;
- sinais determinísticos;
- alternativas possíveis quando a ferramenta falha.

Código de saída zero não é interpretado automaticamente como saúde.

## 5. Reflexão e replanejamento

Uma etapa de análise avalia a rodada e atualiza:

- fatos sustentados;
- hipóteses confirmadas;
- hipóteses descartadas;
- perguntas restantes;
- necessidade de novas evidências;
- confiança atual.

A investigação continua quando a confiança é baixa, existe contradição ou algum critério de sucesso ainda não foi coberto.

## 6. Contratos de saída

Cada função cognitiva possui um contrato JSON. Uma resposta é rejeitada quando:

- não é um objeto JSON;
- usa status fora do domínio permitido;
- omite campos essenciais;
- retorna listas como texto;
- usa confiança fora do intervalo;
- produz plano sem estrutura válida.

Uma resposta inválida é tratada como falha do provedor, não como decisão operacional.

## 7. Failover durante o raciocínio

O preflight antes do SSH continua obrigatório. Além disso, cada etapa cognitiva pode trocar de provedor durante a mesma investigação.

Exemplo:

```text
Groq planeja a rodada
Groq falha na análise por indisponibilidade
OmniRoute assume a análise
Gemini ou Ollama podem assumir outra etapa se necessário
```

A ordem é configurada por `AI_AUTO_PROVIDER_ORDER`. A quantidade máxima de tentativas por etapa é controlada por `AGENT_REASONING_MAX_PROVIDER_ATTEMPTS`.

## 8. IA crítica independente

Após a conclusão, uma IA crítica recebe a missão, critérios de sucesso, conclusão, evidências e avaliações das rodadas.

Ela verifica:

- cobertura de evidências;
- afirmações sem sustentação;
- contradições;
- lacunas ainda abertas;
- segurança da proposta.

Quando a cobertura fica abaixo de `AGENT_CRITIC_MIN_COVERAGE`, a conclusão é marcada como inconclusiva e qualquer token de aprovação é removido do resultado.

## 9. Memória operacional

Investigações anteriores são contexto, não prova. O histórico pode sugerir um caminho ou aumentar a prioridade de uma ferramenta, mas a causa precisa ser confirmada novamente no alvo atual.

## 10. Limites de segurança

O raciocínio dinâmico não altera as políticas:

- produção e standby não recebem correção automática;
- reboot e shutdown são bloqueados;
- banco de cliente não é acessado;
- ciclo de vida de containers não é alterado;
- correções exigem ferramenta permitida no playbook;
- segunda IA, token, aprovação humana e pós-validação continuam obrigatórios.

## Configuração

```env
AGENT_INTELLIGENT_REASONING_ENABLED=true
AGENT_REASONING_PROVIDER_FALLBACK=true
AGENT_REASONING_MAX_PROVIDER_ATTEMPTS=3
AGENT_CRITIC_ENABLED=true
AGENT_CRITIC_MIN_COVERAGE=70
AGENT_PLAYBOOK_ADVISORY_ONLY=true
```
