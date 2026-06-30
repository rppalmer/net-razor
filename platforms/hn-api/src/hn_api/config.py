from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "platforms/hn-api/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8012, ge=1, le=65535)
    hn_api_base_url: str = "https://hn.algolia.com/api/v1"
    request_timeout_seconds: float = Field(default=20, gt=0)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
