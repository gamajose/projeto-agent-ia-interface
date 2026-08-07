# Agent IA Infra

## Objetivo

Este repositório automatiza investigação N1/N2 de infraestrutura e monitoramento.
Priorize diagnóstico baseado em evidências, baixo impacto e rastreabilidade.

## Limites operacionais obrigatórios

- Investigações e coleta somente leitura podem ser automáticas.
- Nunca execute correção, restart, reload, alteração de configuração ou rollback sem
  uma aprovação humana explícita e vinculada às ações exatas.
- Produção e standby podem receber somente **ajustes operacionais seguros e restritos**
  depois da revisão da segunda IA e da aprovação humana explícita. Nunca execute
  correção autônoma nesses ambientes durante a investigação.
- Ambientes desconhecidos recebem somente investigação e proposta até serem classificados.
- Nunca acesse bancos de dados de clientes.
- Nunca reinicie ou desligue hosts.
- Nunca controle de forma destrutiva o ciclo de vida de containers.
- Nunca execute remoções, desinstalações ou paradas destrutivas automaticamente.
- Não reduza as validações de `known_hosts`, a revisão por segunda IA, a lista positiva
  de ferramentas, a assinatura das aprovações ou a pós-validação.
- Não grave senhas, tokens, chaves ou evidências sensíveis no Git.

## Desenvolvimento

- Preserve a separação entre análise, proposta, aprovação e execução.
- Prefira ferramentas estruturadas a comandos shell livres.
- Toda nova ação corretiva precisa de política, precondição, aprovação, rollback seguro
  quando aplicável e validação funcional.
- Em produção e standby, qualquer ação permitida deve permanecer vinculada ao token de
  aprovação, ao alvo e aos argumentos exatos, seguida de pós-validação.
- Antes de concluir uma mudança, execute:

```bash
python -m compileall -q app tests labs
pytest -q
```
