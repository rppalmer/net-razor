from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "platforms/yt-api/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8013, ge=1, le=65535)
    yt_transcript_proxy_url: SecretStr | None = None
    log_level: str = "INFO"

    @property
    def proxy_url_value(self) -> str | None:
        if self.yt_transcript_proxy_url is None:
            return None
        value = self.yt_transcript_proxy_url.get_secret_value().strip()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
