from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    secret_backend: str = "env"
    vault_addr: str | None = None
    vault_token: str | None = None
    vault_namespace: str | None = None
    vault_kv_mount: str = "secret"
    vault_secret_path: str = "agent-ia"
    vault_verify_tls: bool = True
    vault_cache_seconds: int = 60

    ssh_default_user: str = "2com"
    ssh_default_password: str | None = None
    ssh_private_key_path: str | None = None
    ssh_private_key_passphrase: str | None = None
    ssh_allow_agent: bool = True
    ssh_look_for_keys: bool = True
    ssh_default_port: int = 22
    ssh_connect_timeout: int = 15
    ssh_command_timeout: int = 60
    ssh_strict_host_key_checking: bool = True
    ssh_known_hosts_path: str = "~/.ssh/known_hosts"

    # O servidor VPN funciona como bastion SSH. Os nomes SSH_SRV_VPN_*
    # preservam compatibilidade com os ambientes já usados pelos operadores.
    ssh_bastion_host: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SSH_BASTION_HOST", "SSH_SRV_VPN_IP", "SSH_SRV_VPN"),
    )
    ssh_bastion_port: int = Field(
        default=22,
        validation_alias=AliasChoices("SSH_BASTION_PORT", "SSH_SRV_VPN_PORT", "SSH_PORT_SRV_VPN"),
    )
    ssh_bastion_user: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SSH_BASTION_USER", "SSH_SRV_VPN_USER", "SSH_USER_SRV_VPN"),
    )
    ssh_bastion_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SSH_BASTION_PASSWORD",
            "SSH_SRV_VPN_SENHA",
            "SSH_PASSWORD_SRV_VPN",
        ),
    )
    ssh_bastion_private_key_path: str | None = None
    ssh_bastion_private_key_passphrase: str | None = None

    postgres_dsn: str = Field(...)
    redis_url: str = "redis://127.0.0.1:6379/1"

    agent_execution_mode: str = "inline"
    agent_queue_name: str = "agent-ia:jobs"
    agent_result_prefix: str = "agent-ia:result:"
    agent_worker_name: str = "default"
    agent_job_ttl_seconds: int = 86400
    agent_queue_block_seconds: int = 5

    # Execução em lote usa a mesma API e o mesmo motor por alvo. A concorrência
    # limita quantas investigações a interface mantém simultaneamente.
    agent_batch_enabled: bool = True
    agent_batch_max_targets: int = Field(default=50, ge=1, le=500)
    agent_batch_concurrency: int = Field(default=2, ge=1, le=10)
    agent_batch_max_file_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)

    checkmk_api_user: str | None = None
    checkmk_api_secret: str | None = None
    checkmk_webhook_token: str | None = None
    checkmk_webhook_auto_correct: bool = False

    agent_api_token: str | None = None
    agent_default_mode: str = "propose"
    agent_autopilot_enabled: bool = True
    agent_autopilot_default: bool = True
    agent_runtime_discovery_enabled: bool = True
    agent_adaptive_tools_enabled: bool = True
    agent_tool_recommendation_limit: int = Field(default=10, ge=3, le=30)

    # Núcleo cognitivo: interpreta a missão, valida os contratos JSON de cada
    # etapa, troca de provedor quando uma IA falha e audita a conclusão com uma
    # IA crítica independente. O playbook vira contexto, não sequência fixa.
    agent_intelligent_reasoning_enabled: bool = True
    agent_reasoning_provider_fallback: bool = True
    agent_reasoning_max_provider_attempts: int = Field(default=3, ge=1, le=5)
    agent_critic_enabled: bool = True
    agent_critic_min_coverage: int = Field(default=70, ge=40, le=100)
    agent_playbook_advisory_only: bool = True

    agent_max_rounds: int = 5
    agent_max_commands: int = 20
    agent_min_confidence: int = 70
    agent_playbook_dir: str = str(PROJECT_ROOT / "config" / "playbooks")
    agent_allow_legacy_read_commands: bool = True

    # Recuperação adaptativa: depois da aprovação humana, cada ação é observada.
    # Falhas viram novas evidências, ferramentas de leitura mapeiam o bloqueio e
    # outra ação só é executada quando continua dentro do envelope aprovado.
    agent_recovery_enabled: bool = True
    agent_recovery_max_rounds: int = Field(default=3, ge=1, le=8)
    agent_recovery_max_actions: int = Field(default=6, ge=1, le=20)
    agent_recovery_max_diagnostics_per_round: int = Field(default=4, ge=1, le=8)
    agent_recovery_max_repeated_action: int = Field(default=2, ge=1, le=3)

    ai_provider: str = "gemini"
    ai_auto_provider_order: str = "groq,omniroute,deepseek,gemini,ollama,openrouter"
    ai_preflight_timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    # O catálogo dinâmico guarda apenas metadados. As chaves continuam no .env
    # ou Vault e nunca são devolvidas para o navegador.
    ai_provider_registry_path: str = "~/.config/agent-ia/providers.json"
    ai_settings_env_path: str = ""
    ai_settings_ui_enabled: bool = True
    ai_settings_allow_secret_write: bool = True

    # O Gemini consulta a lista de modelos visível para a própria chave. Quando
    # o modelo configurado não existir, usa o primeiro modelo desta lista que
    # esteja disponível. Em 429/5xx tenta o próximo modelo da lista.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_auto_free: bool = True
    gemini_transient_fallback: bool = True
    gemini_free_models: str = (
        "gemini-3.5-flash,gemini-3.1-flash-lite,"
        "gemini-2.5-flash,gemini-2.5-flash-lite"
    )

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # DeepSeek usa a API OpenAI-compatible oficial. Os aliases antigos
    # deepseek-chat e deepseek-reasoner foram substituídos pelos modelos V4.
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_models: str = "deepseek-v4-flash,deepseek-v4-pro"
    deepseek_base_url: str = "https://api.deepseek.com"

    openrouter_api_key: str | None = None
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_app_name: str = "Agent IA Infra"
    openrouter_site_url: str | None = None

    # O modelo do projeto anterior era gemma3:4b. Se ele não estiver disponível,
    # o Agent pode selecionar outro modelo já instalado no Ollama. A listagem
    # usa um probe rápido; a geração real pode aguardar mais no primeiro load.
    ollama_model: str = "gemma3:4b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_auto_fallback: bool = True
    ollama_preferred_models: str = "gemma3:4b,llama3.2"
    ollama_preflight_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)

    # OmniRoute é um gateway: o Agent precisa apenas do token e da URL.
    # A rota/modelo é selecionada no menu, por OMNIROUTE_DEFAULT_ROUTE ou
    # pela lista opcional OMNIROUTE_ROUTES. OMNIROUTE_MODEL permanece como
    # alias legado para instalações anteriores.
    omniroute_api_key: str | None = None
    omniroute_base_url: str = "http://127.0.0.1:20128/v1"
    omniroute_default_route: str = ""
    omniroute_routes: str = ""
    omniroute_model: str = ""

    ai_reviewer_provider: str = "groq"
    ai_reviewer_model: str = ""
    ai_reviewer_required_for_corrections: bool = True
    ai_reviewer_min_confidence: int = 80

    approval_secret: str | None = None
    approval_ttl_minutes: int = 30

    helpdesk_webhook_url: str | None = None
    helpdesk_webhook_token: str | None = None
    helpdesk_publish_automatically: bool = False

    codex_cli_path: str | None = None
    codex_workdir: str | None = None
    codex_home: str | None = None

    # OpenCode usa OmniRoute, mas permanece separado do motor de troubleshooting.
    # A interface integrada executa `opencode run` no diretório configurado e
    # nunca recebe automaticamente SSH, bastion ou credenciais de servidores.
    opencode_enabled: bool = True
    opencode_cli_path: str | None = None
    opencode_workdir: str | None = None
    opencode_config_path: str = "~/.config/opencode/opencode.json"
    opencode_model: str = ""
    opencode_small_model: str = ""
    opencode_default_agent: str = "plan"
    opencode_web_host: str = "127.0.0.1"
    opencode_web_port: int = Field(default=4096, ge=1024, le=65535)
    opencode_web_url: str = "http://127.0.0.1:4096"
    opencode_server_username: str = "opencode"
    opencode_server_password: str | None = None
    opencode_tunnel_host: str | None = None
    opencode_tunnel_ssh_port: int = Field(default=22, ge=1, le=65535)
    opencode_tunnel_user: str | None = None
    opencode_interface_enabled: bool = True
    opencode_interface_allow_build: bool = True
    opencode_run_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    opencode_run_max_prompt_chars: int = Field(default=12000, ge=100, le=50000)
    opencode_run_max_output_chars: int = Field(default=250000, ge=10000, le=2_000_000)
    opencode_run_concurrency: int = Field(default=1, ge=1, le=4)

    recurrence_warning_count: int = 2
    recurrence_warning_days: int = 7
    recurrence_critical_count: int = 4
    recurrence_critical_days: int = 30

    filesystem_warning_percent: int = 80
    filesystem_critical_percent: int = 90
    inode_warning_percent: int = 80
    inode_critical_percent: int = 90
    load_warning_ratio: int = 1
    load_critical_ratio: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
