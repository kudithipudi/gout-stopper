from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    root_path: str = "/gout-stopper"
    db_path: str = "data/gout-stopper.db"
    uploads_dir: str = "data/uploads"
    openrouter_api_key: str = ""
    admin_password: str = ""
    session_secret: str = ""

    # One model per LLM purpose (§ "different LLMs for different purposes").
    food_detect_model: str = "google/gemini-3.1-flash-lite"
    food_identify_model: str = "google/gemini-3.1-flash-lite"
    advice_model: str = "google/gemini-3.1-flash-lite"
    # Rates a food for gout risk when it isn't on the admin list or the
    # feedback-trained learned list. Off -> unmatched foods stay "unknown".
    gout_classify_model: str = "google/gemini-3.1-flash-lite"
    gout_classify_enabled: bool = True
    llm_temperature: float = 0.0
    llm_timeout: int = 120
    max_upload_bytes: int = 20 * 1024 * 1024
    scan_rate_limit_per_minute: int = 6
    # Reuse a recent identical scan's results instead of re-running the LLM
    # pipeline. The model-id triplet is part of the cache key, so changing a
    # model busts it. Set 0/false to always analyze fresh.
    scan_cache_enabled: bool = True
    scan_cache_max_age_hours: int = 720
    # Per-IP cap on POST /admin/login. Low enough to make online brute force
    # of the single shared admin password pointless, high enough that a human
    # mistyping their password a few times isn't locked out.
    admin_login_rate_limit_per_minute: int = 5
    log_level: str = "info"


def get_settings() -> Settings:
    # Not cached: this app runs a single gunicorn worker and Settings() is cheap
    # to build, so we always read the current environment/.env rather than risk
    # a stale cached instance (e.g. across tests that monkeypatch env vars).
    return Settings()
