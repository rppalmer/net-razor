from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "platforms/yt-api/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8013, ge=1, le=65535)
    yt_transcript_proxy_url: SecretStr | None = None
    youtube_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_API_KEY", "YT_API_KEY"),
    )
    youtube_api_base_url: str = "https://www.googleapis.com"
    yt_search_mode: Literal["broad", "channels"] = "broad"
    youtube_channel_ids: str = Field(
        default="",
        validation_alias=AliasChoices("YOUTUBE_CHANNEL_IDS", "YT_CHANNEL_IDS"),
    )
    request_timeout_seconds: float = Field(default=30, gt=0)
    log_level: str = "INFO"

    @property
    def proxy_url_value(self) -> str | None:
        if self.yt_transcript_proxy_url is None:
            return None
        value = self.yt_transcript_proxy_url.get_secret_value().strip()
        return value or None

    @property
    def youtube_api_key_value(self) -> str | None:
        if self.youtube_api_key is None:
            return None
        value = self.youtube_api_key.get_secret_value().strip()
        return value or None

    @property
    def youtube_search_configured(self) -> bool:
        if self.youtube_api_key_value is None:
            return False
        if self.yt_search_mode == "channels":
            return bool(self.youtube_channel_id_list)
        return True

    @property
    def youtube_channel_id_list(self) -> list[str]:
        raw = self.youtube_channel_ids.replace("\n", ",")
        return [channel_id.strip() for channel_id in raw.split(",") if channel_id.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
