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
    food_detect_model: str = "openai/gpt-4o-mini"
    food_identify_model: str = "openai/gpt-4o-mini"
    advice_model: str = "openai/gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_timeout: int = 120
    max_upload_bytes: int = 20 * 1024 * 1024


def get_settings() -> Settings:
    # Not cached: this app runs a single gunicorn worker and Settings() is cheap
    # to build, so we always read the current environment/.env rather than risk
    # a stale cached instance (e.g. across tests that monkeypatch env vars).
    return Settings()
