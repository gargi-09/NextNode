from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── secrets (from the environment / .env) ──
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str | None = None  # Aura credential files include this; None = server default
    gemini_api_key: str

    # ── Gemini free-tier budget (quotas are per Google Cloud project) ──
    # Defaults sit below the most conservative published free-tier numbers;
    # set them from the limits shown in your AI Studio console.
    gemini_model: str = "gemini-2.5-flash"
    gemini_rpm: int = 8
    gemini_rpd: int = 200
    gemini_explain_ceiling: float = 0.8  # explanations stop at 80% of the daily budget
    gemini_timeout_s: int = 20
    llm_cache_max_entries: int = 512

    # ── Neo4j Aura Free resilience ──
    neo4j_connection_timeout_s: float = 5.0
    neo4j_acquisition_timeout_s: float = 10.0
    neo4j_max_retry_time_s: float = 8.0
    neo4j_liveness_check_timeout_s: float = 30.0
    neo4j_max_connection_lifetime_s: float = 300.0
    neo4j_pool_size: int = 5
    breaker_failure_threshold: int = 3
    breaker_window_s: float = 60.0
    breaker_cooldown_s: float = 30.0

    # Aura's downloaded credentials file also carries AURA_INSTANCEID / AURA_INSTANCENAME,
    # which the app doesn't use — ignore unknown keys instead of failing at startup.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
