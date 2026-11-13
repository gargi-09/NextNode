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
    gemini_timeout_s: int = 30
    # Extraction copies skills out of text: no reasoning needed, and 2.5 Flash's default thinking
    # made long resumes miss the deadline (504). 0 turns it off; None leaves the model default
    # (needed for models that cannot disable thinking).
    gemini_thinking_budget: int | None = 0
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

    # ── Public mode (a hosted instance strangers can use) ──
    # Off: every route works, for local use. On: only the public analyze flow, the read-only
    # ontology and health are served; nothing a visitor sends is kept past their request.
    public_mode: bool = False
    public_origins: str = "https://gargi-09.github.io"  # comma-separated CORS origins
    public_rate_per_minute: int = 3                       # analyses per client IP
    public_rate_per_day: int = 20
    public_sweep_age_s: int = 3600       # visitor nodes left by a crashed request are deleted after this
    public_trust_forwarded_for: bool = True  # behind Cloud Run: client IP = last X-Forwarded-For hop

    # Aura's downloaded credentials file also carries AURA_INSTANCEID / AURA_INSTANCENAME,
    # which the app doesn't use — ignore unknown keys instead of failing at startup.
    # Values are stripped: a secret stored with a trailing newline (common when piped into
    # Secret Manager from a shell) must not break the Neo4j URI or the API key.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", str_strip_whitespace=True)
