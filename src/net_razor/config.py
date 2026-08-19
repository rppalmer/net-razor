from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from net_razor.paths import find_repo_root
from net_razor.sources.yt.channel_ref import ChannelRef, parse_channel_refs

_REPO_ROOT = find_repo_root(Path(__file__))

# Credentials and operator data live in a fixed home directory, not the checkout.
# An MCP host chooses the working directory and passes a narrow environment, so
# anything resolved from the checkout is only found when something happens to
# launch from the right place. A home path resolves identically from VS Code, an
# agent, or a service, survives a re-clone, and keeps secrets out of a directory
# that is under version control.
_HOME_ROOT = Path.home() / ".net-razor"

# Secrets and toggles live in .env; the channel list lives in its own file.
# Keeping the list out of .env removes a whole class of dotenv formatting traps:
# a multi-line value had to be double-quoted or only its first line was read, and
# a `#` comment inside the quotes silently swallowed an entry.
_ENV_FILES = (_HOME_ROOT / ".env",)
_CHANNELS_FILE = _HOME_ROOT / "channels.txt"


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

    # YouTube
    youtube_api_key: SecretStr | None = None
    yt_search_mode: str = Field(default="broad")
    # Channels come from channels.txt. Point this elsewhere only if you need to.
    channels_file: Path = _CHANNELS_FILE
    yt_proxy_url: SecretStr | None = None
    # Default for the channel digest's cross-run dedup when a call doesn't set only_new.
    yt_digest_only_new: bool = False
    # Default for skipping videos without a fetchable transcript (e.g. captions disabled).
    yt_digest_require_transcript: bool = False
    # Max characters of transcript text returned per video (0 = no cap). ~40k chars
    # (~10k tokens) covers a ~35-minute video at normal speaking pace; longer videos are
    # truncated (and flagged). Bounds LLM context regardless of agent/host behavior.
    yt_max_transcript_chars: int = Field(default=40000, ge=0)

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

    @field_validator("channels_file")
    @classmethod
    def _resolve_channels_file(cls, value: Path) -> Path:
        return value if value.is_absolute() else _HOME_ROOT / value

    @field_validator("podcasts_file")
    @classmethod
    def _resolve_podcasts_file(cls, value: Path) -> Path:
        return value if value.is_absolute() else _HOME_ROOT / value

    @field_validator("yt_search_mode")
    @classmethod
    def _validate_search_mode(cls, value: str) -> str:
        # Fail loudly: silently falling back to "broad" on a typo meant a
        # channel-restricted search quietly returned all of YouTube instead.
        mode = value.strip().lower()
        if mode not in {"broad", "channels"}:
            raise ValueError(
                f"YT_SEARCH_MODE must be 'broad' or 'channels', got {value!r}"
            )
        return mode

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
        return self._secret(self.yt_proxy_url)

    @property
    def youtube_channel_refs(self) -> list[ChannelRef]:
        """Channels from ``channels.txt`` — one per line, ``#`` comments allowed.

        Missing file means no configured channels, which the tools report as a
        clear caveat rather than an error.
        """
        if not self.channels_file.is_file():
            return []
        lines = []
        for raw_line in self.channels_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()  # strip whole-line and trailing comments
            if line:
                lines.append(line)
        return parse_channel_refs("\n".join(lines))

    @property
    def youtube_search_configured(self) -> bool:
        if self.youtube_api_key_value is None:
            return False
        if self.yt_search_mode == "channels":
            return bool(self.youtube_channel_refs)
        return True

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
