"""
config.py — Central configuration using pydantic-settings.
All secrets come from environment variables. Never hardcoded.
"""
import json
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
    allowed_origins: str = "*"                    # comma-separated: https://app.company.com,https://admin.company.com
    log_level: str = "INFO"
    audit_log_path: str = "./logs/audit.log"

    # Scheduler
    scheduler_enabled: bool = True
    extract_cron_daily: str = "0 2 * * *"
    snapshot_retention_days: int = 400

    # Jira customfield IDs (differ per Jira instance)
    jira_field_epic_link: str = "customfield_10014"
    jira_field_story_points: str = "customfield_10016"
    jira_field_sprint: str = "customfield_10020"

    # Cycle / lead time computation
    jira_in_progress_statuses: str = "In Progress"

    # Risk scoring weights (JSON: {"delivery":0.30, "quality":0.35, "compliance":0.20, "operational":0.15})
    risk_weights_json: str = '{"delivery": 0.30, "quality": 0.35, "compliance": 0.20, "operational": 0.15}'

    @property
    def risk_weights(self) -> dict[str, float]:
        """Parse risk_weights_json into a dict, validated at access time."""
        try:
            w = json.loads(self.risk_weights_json)
            if not isinstance(w, dict):
                raise ValueError("risk_weights must be a JSON object")
            total = sum(w.values())
            if abs(total - 1.0) > 0.01:
                raise ValueError(f"Risk weights must sum to ~1.0, got {total:.2f}")
            return w
        except (json.JSONDecodeError, ValueError) as e:
            import warnings
            warnings.warn(f"Invalid RISK_WEIGHTS: {e}. Using defaults.")
            return {"delivery": 0.30, "quality": 0.35, "compliance": 0.20, "operational": 0.15}

    # Features
    enable_nlp_analysis: bool = False
    enable_duplicate_detection: bool = False
    enable_pgvector: bool = False
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_batch_size: int = 32

    # AI Agent
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.3
    agent_context_size: int = 10

    def masked_token(self) -> str:
        """Returns masked API token for safe logging."""
        t = self.jira_api_token
        if len(t) > 8:
            return t[:4] + "****" + t[-4:]
        return "****"


@lru_cache
def get_settings() -> Settings:
    return Settings()
