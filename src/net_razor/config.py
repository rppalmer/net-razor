from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from net_razor.paths import find_repo_root

_REPO_ROOT = find_repo_root(Path(__file__))

# Credentials and operator data live in a fixed home directory, not the checkout.
# An MCP host chooses the working directory and passes a narrow environment, so
# anything resolved from the checkout is only found when something happens to
# launch from the right place. A home path resolves identically from VS Code, an
# agent, or a service, survives a re-clone, and keeps secrets out of a directory
# that is under version control.
_HOME_ROOT = Path.home() / ".net-razor"

# Secrets and toggles live in .env; the podcast feed list lives in its own file.
# Keeping the list out of .env removes a whole class of dotenv formatting traps:
# a multi-line value had to be double-quoted or only its first line was read, and
# a `#` comment inside the quotes silently swallowed an entry.
_ENV_FILES = (_HOME_ROOT / ".env",)


class Settings(BaseSettings):
    """One composed settings object. Resolved once at the composition root."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # storage / runtime
    database_path: Path = _HOME_ROOT / "data" / "net_razor_audit.db"
    log_level: str = "INFO"
    # Optional log file. Under an MCP host, the server's stderr is often discarded, so
    # set LOG_FILE to capture logs reliably (e.g. logs/net-razor.log).
    log_file: Path | None = None

    # X
    auth_token: SecretStr | None = None
    ct0: SecretStr | None = None
    node_binary: str = "node"

    # podcasts
    podcasts_file: Path = _HOME_ROOT / "podcasts.txt"
    podcast_max_transcript_chars: int = Field(default=12000, ge=1000)
    # Off by default: it needs mlx (Apple Silicon only), ffmpeg, and a 1.5 GB model.
    # When off, podcast_whisper_transcript reports not_configured and nothing else
    # in the server notices.
    podcast_whisper_enabled: bool = False
    podcast_whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    # The longest configured show is 184 minutes, which measures at 8.3 minutes of
    # transcription. This is the ceiling before the subprocess is killed, sized so
    # the consumer's own timeout never fires first.
    podcast_whisper_timeout_seconds: float = Field(default=900, gt=0)
    # A three-hour episode is around 170MB; this bounds a mislabelled feed.
    podcast_max_audio_bytes: int = Field(default=500 * 1024 * 1024, gt=0)
    podcast_audio_timeout_seconds: float = Field(default=300, gt=0)

    # shared
    request_timeout_seconds: float = Field(default=30, gt=0)

    # A relative override resolves against the home directory, not the checkout,
    # for the same reason the defaults live there: the checkout is not where the
    # process necessarily starts, and operator data does not belong in it.
    @field_validator("database_path")
    @classmethod
    def _resolve_database_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else _HOME_ROOT / value

    @field_validator("log_file")
    @classmethod
    def _resolve_log_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value if value.is_absolute() else _HOME_ROOT / value

    @field_validator("podcasts_file")
    @classmethod
    def _resolve_podcasts_file(cls, value: Path) -> Path:
        return value if value.is_absolute() else _HOME_ROOT / value

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
    def repo_root(self) -> Path:
        """The checkout. Locates code — the vendored X backend — never data."""
        return _REPO_ROOT

    @property
    def home_root(self) -> Path:
        """Where credentials and operator data live, independent of the checkout."""
        return _HOME_ROOT


@lru_cache
def get_settings() -> Settings:
    return Settings()
