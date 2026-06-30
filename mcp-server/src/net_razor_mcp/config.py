from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "mcp-server/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    orchestrator_base_url: str = "http://127.0.0.1:8010"
    x_api_base_url: str = "http://127.0.0.1:8011"
    hn_api_base_url: str = "http://127.0.0.1:8012"
    yt_api_base_url: str = "http://127.0.0.1:8013"
    request_timeout_seconds: float = Field(default=60, gt=0)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
