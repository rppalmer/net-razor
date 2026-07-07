from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from net_razor.paths import find_repo_root
from net_razor.sources.yt.channel_ref import ChannelRef, parse_channel_refs

_REPO_ROOT = find_repo_root(Path(__file__))

# All configuration lives in a single root .env.
_ENV_FILES = (_REPO_ROOT / ".env",)


class Settings(BaseSettings):
    """One composed settings object. Resolved once at the composition root."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # storage / runtime
    database_path: Path = _REPO_ROOT / "data" / "net_razor_audit.db"
    log_level: str = "INFO"

    # X
    auth_token: SecretStr | None = None
    ct0: SecretStr | None = None
    node_binary: str = "node"
    x_search_subprocess_timeout_seconds: float = Field(default=45, gt=0)
    x_search_upstream_timeout_seconds: float = Field(default=20, gt=0)
    x_search_max_attempts: int = Field(default=3, ge=1, le=5)
    x_search_retry_backoff_seconds: float = Field(default=1, ge=0)
    x_search_delay_seconds: float = Field(default=1, ge=0)

    # HN
    hn_algolia_base_url: str = Field(
        default="https://hn.algolia.com/api/v1",
        validation_alias=AliasChoices("HN_ALGOLIA_BASE_URL", "HN_API_BASE_URL"),
    )

    # YouTube
    youtube_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_API_KEY", "YT_API_KEY"),
    )
    youtube_api_base_url: str = "https://www.googleapis.com"
    yt_search_mode: str = Field(default="broad")
    youtube_channel_ids: str = Field(
        default="",
        validation_alias=AliasChoices("YOUTUBE_CHANNEL_IDS", "YT_CHANNEL_IDS"),
    )
    yt_transcript_proxy_url: SecretStr | None = None

    # shared
    request_timeout_seconds: float = Field(default=30, gt=0)

    @field_validator("database_path")
    @classmethod
    def _resolve_database_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else _REPO_ROOT / value

    @field_validator("yt_search_mode")
    @classmethod
    def _validate_search_mode(cls, value: str) -> str:
        value = value.strip().lower()
        return value if value in {"broad", "channels"} else "broad"

    # -- derived accessors ---------------------------------------------------
    @staticmethod
    def _secret(value: SecretStr | None) -> str | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        return secret or None

    @property
    def auth_token_value(self) -> str | None:
        return self._secret(self.auth_token)

    @property
    def ct0_value(self) -> str | None:
        return self._secret(self.ct0)

    @property
    def x_credentials_configured(self) -> bool:
        return bool(self.auth_token_value and self.ct0_value)

    @property
    def youtube_api_key_value(self) -> str | None:
        return self._secret(self.youtube_api_key)

    @property
    def proxy_url_value(self) -> str | None:
        return self._secret(self.yt_transcript_proxy_url)

    @property
    def youtube_channel_id_list(self) -> list[str]:
        raw = self.youtube_channel_ids.replace("\n", ",")
        return [channel.strip() for channel in raw.split(",") if channel.strip()]

    @property
    def youtube_channel_refs(self) -> list[ChannelRef]:
        """Configured channels parsed into refs (IDs, @handles, or URLs)."""
        return parse_channel_refs(self.youtube_channel_ids)

    @property
    def youtube_search_configured(self) -> bool:
        if self.youtube_api_key_value is None:
            return False
        if self.yt_search_mode == "channels":
            return bool(self.youtube_channel_refs)
        return True

    @property
    def repo_root(self) -> Path:
        return _REPO_ROOT


@lru_cache
def get_settings() -> Settings:
    return Settings()
