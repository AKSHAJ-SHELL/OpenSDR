from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    llm_provider: str = "anthropic"  # anthropic | ollama | mock
    anthropic_model: str = "claude-sonnet-4-6"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b"

    # Embeddings
    embedding_provider: str = "hash"  # voyage | hash
    voyage_api_key: str = ""
    voyage_model: str = "voyage-3.5-lite"
    embedding_dim: int = 1024

    # Infra
    database_url: str = "postgresql+psycopg://craftsman:craftsman@localhost:5432/craftsman"
    redis_url: str = "redis://localhost:6379/0"

    # Enrichment (M2.1) — bring your own keys. `enrichment_providers` is the
    # comma-separated precedence order (e.g. "apollo,hunter"); empty = enrichment
    # disabled, pipeline is verify-only. A listed provider with a blank key is skipped.
    apollo_api_key: str = ""
    hunter_api_key: str = ""
    enrichment_providers: str = ""

    # Lead sourcing (M2.2) — BYO keys. `lead_source_providers` is the comma-separated set
    # of enabled sources (e.g. "apollo,webhook"); empty = sourcing disabled. Apollo reuses
    # `apollo_api_key`; the webhook source GETs `lead_source_webhook_url` (https only).
    lead_source_providers: str = ""
    lead_source_webhook_url: str = ""

    # Secrets
    craftsman_secret_key: str = ""

    # Sending / compliance
    unsubscribe_base_url: str = "http://localhost:8000"
    physical_address: str = "123 Main St, San Francisco, CA 94105"
    gdpr_mode: bool = False
    icp_threshold: float = 0.55

    # Business hours for sends (lead-local)
    send_window_start_hour: int = 9
    send_window_end_hour: int = 16
    send_window_end_minute: int = 30
    send_jitter_minutes: int = 20

    # Bandit
    bandit_deactivate_min_trials: int = 30
    # Reproducibility knob for sims/CI: when set, pick_arm draws from a deterministic
    # stream. Leave UNSET in multi-worker prod (every process would seed identically).
    bandit_seed: int | None = None

    # Classifier
    classifier_confidence_threshold: float = 0.7

    # Re-drive: an outbound claim with sent_at still NULL after this many minutes is a
    # crash residual — the sweep deletes it, frees the campaign slot, and re-readies the lead.
    redrive_unsent_after_minutes: int = 15

    # Notifications
    slack_webhook_url: str = ""

    # Local sandbox: when set, poll_inboxes also reads Mailpit's HTTP API
    # (e.g. http://localhost:8025 or http://mailpit:8025 in Docker).
    mailpit_url: str = ""

    # Dry-run (M1.2) delivers to Mailpit's SMTP regardless of configured mailboxes.
    mailpit_smtp_host: str = "localhost"  # "mailpit" in Docker
    mailpit_smtp_port: int = 1025


@lru_cache
def get_settings() -> Settings:
    return Settings()
