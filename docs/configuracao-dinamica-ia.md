# Configuração dinâmica de provedores de IA

A aba **Configurações** permite administrar os provedores usados pelo Agent IA sem editar o `.env` manualmente.

## Fluxo

1. O operador clica em **Adicionar IA** ou em **Configurar** no card de um provedor.
2. O formulário abre em um modal, sem criar uma seção permanente no fim da página.
3. O operador cadastra ou atualiza chave, endpoint e modelos.
4. A chave é gravada somente no backend configurado (`.env` ou Vault).
5. O catálogo público recebe apenas nome, endpoint, modelos, tipo de uso e estado configurado.
6. O diagnóstico valida credencial, modelo e resposta JSON.
7. O provedor aparece em **Nova investigação** e pode participar da ordem automática.
8. Antes de qualquer SSH, o preflight completo continua obrigatório.

## Provedores nativos

- Google Gemini
- Groq
- DeepSeek
- OpenRouter
- Ollama local
- OmniRoute

Os provedores nativos podem ser configurados, mas não removidos. A chave existente é preservada quando o campo de senha fica vazio.

## DeepSeek

Configuração padrão:

```env
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_MODELS=deepseek-v4-flash,deepseek-v4-pro
```

O DeepSeek usa a integração OpenAI-compatible do Agent. Depois que a chave for detectada, ele passa a aparecer no seletor de IA e pode participar do autopilot conforme `AI_AUTO_PROVIDER_ORDER`.

## Provedor personalizado

Um provedor personalizado deve oferecer o endpoint:

```text
POST <BASE_URL>/chat/completions
```

E aceitar o formato OpenAI-compatible com:

```json
{
  "model": "modelo-principal",
  "messages": [{"role": "user", "content": "..."}],
  "response_format": {"type": "json_object"}
}
```

Exemplo de cadastro no modal **Adicionar IA**:

```text
Identificador: mistral-interno
Nome: Mistral Interno
Base URL: https://ia.interna.exemplo/v1
Modelo padrão: modelo-a
Modelos: modelo-a, modelo-b
Tipo de uso: personalizado
```

A chave será armazenada em:

```text
AI_PROVIDER_MISTRAL_INTERNO_API_KEY
```

O arquivo `providers.json` nunca contém a credencial.

## OmniRoute

A configuração do OmniRoute continua separada dos provedores diretos:

```env
OMNIROUTE_API_KEY=
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
OMNIROUTE_DEFAULT_ROUTE=auto/coding
OMNIROUTE_ROUTES=Código=auto/coding,Rápido=auto/fast
```

Cadastrar uma chave direta no Agent não injeta automaticamente essa chave dentro do container OmniRoute. O Agent passa a usar o provedor diretamente. Para usar a mesma IA por meio do gateway, ela também deve ser cadastrada no próprio OmniRoute e publicada em uma rota.

## Autopilot e ordem dos cards

A grade de provedores funciona como um trilho Kanban horizontal. O card mais à esquerda tem a maior prioridade. Ao arrastar um card e soltá-lo em outra posição, a interface salva automaticamente a nova sequência.

A ordem continua persistida em:

```env
AI_AUTO_PROVIDER_ORDER=groq,omniroute,deepseek,gemini,ollama,openrouter
```

O autopilot ignora provedores sem credencial ou que falhem no diagnóstico. Cada candidato realiza geração JSON real antes de o Agent abrir a conexão SSH.

## Segurança

- API keys são write-only na interface.
- O navegador recebe apenas `configured=true/false`.
- `.env` e `providers.json` usam permissão `600`.
- As gravações são atômicas.
- O identificador, URL, modelos e tamanho dos valores são validados.
- Requisições de alteração exigem a proteção de origem já usada pela interface.
- Nenhuma regra de produção, standby, reboot, banco de cliente ou aprovação foi alterada.
- `AI_SETTINGS_ALLOW_SECRET_WRITE=false` transforma a aba em modo sem gravação de chaves.

## Processos em fila

Em execução `inline`, a nova configuração entra na próxima investigação. Em modo `queue`, processos worker já iniciados devem ser reiniciados para recarregar o `.env`.
