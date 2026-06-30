from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "orchestrator/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8010, ge=1, le=65535)
    x_api_base_url: str = "http://127.0.0.1:8011"
    hn_api_base_url: str = "http://127.0.0.1:8012"
    yt_api_base_url: str = Field(
        default="http://127.0.0.1:8013",
        validation_alias=AliasChoices("YT_API_BASE_URL", "YOUTUBE_TRANSCRIPTS_API_BASE_URL"),
    )
    database_path: Path = Path("orchestrator/data/net_razor.db")
    request_timeout_seconds: float = Field(default=60, gt=0)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
