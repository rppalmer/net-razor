from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-based service configuration."""

    model_config = SettingsConfigDict(
        env_file=(".env", "platforms/x-api/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8011, ge=1, le=65535)
    auth_token: SecretStr | None = None
    ct0: SecretStr | None = None
    node_binary: str = "node"
    x_search_subprocess_timeout_seconds: float = Field(default=45, gt=0)
    x_search_upstream_timeout_seconds: float = Field(default=20, gt=0)
    x_search_max_attempts: int = Field(default=3, ge=1, le=5)
    x_search_retry_backoff_seconds: float = Field(default=1, ge=0)
    x_search_delay_seconds: float = Field(default=1, ge=0)
    log_level: str = "INFO"

    @staticmethod
    def _secret_value(value: SecretStr | None) -> str | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        return secret or None

    @property
    def auth_token_value(self) -> str | None:
        return self._secret_value(self.auth_token)

    @property
    def ct0_value(self) -> str | None:
        return self._secret_value(self.ct0)

    @property
    def credentials_configured(self) -> bool:
        return bool(self.auth_token_value and self.ct0_value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
