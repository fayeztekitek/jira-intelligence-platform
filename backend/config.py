"""
config.py — Central configuration using pydantic-settings.
All secrets come from environment variables. Never hardcoded.
"""
from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Jira
    jira_base_url: str = "https://your-company.atlassian.net"
    jira_auth_type: Literal["api_token", "pat", "oauth2"] = "api_token"
    jira_username: str = ""
    jira_api_token: str = ""
    jira_pat: str = ""
    jira_page_size: int = 100
    jira_max_results: int = 50000
    jira_rate_limit_delay: float = 0.3
    jira_retry_attempts: int = 3
    jira_retry_backoff: float = 2.0

    # Database
    database_url: str = "sqlite+aiosqlite:///./jira_intelligence.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: str = "dev_secret_key_change_in_production"
    log_level: str = "INFO"
    audit_log_path: str = "./logs/audit.log"

    # Scheduler
    scheduler_enabled: bool = True
    extract_cron_daily: str = "0 2 * * *"
    snapshot_retention_days: int = 400

    # Features
    enable_nlp_analysis: bool = False
    enable_duplicate_detection: bool = False

    def masked_token(self) -> str:
        """Returns masked API token for safe logging."""
        t = self.jira_api_token
        if len(t) > 8:
            return t[:4] + "****" + t[-4:]
        return "****"


@lru_cache
def get_settings() -> Settings:
    return Settings()
