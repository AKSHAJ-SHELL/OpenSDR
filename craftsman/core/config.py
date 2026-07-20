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

    # Classifier
    classifier_confidence_threshold: float = 0.7

    # Notifications
    slack_webhook_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
