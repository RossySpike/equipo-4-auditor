"""Runtime configuration for the AG-AUD auditor service."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEVELOPMENT_SIGNING_KEY = "dev-insecure-auditor-signing-key"
DEFAULT_APPROVAL_THRESHOLD = 85
DEFAULT_TRACE_PATH = Path("data/audit_trace.jsonl")
DEFAULT_AI_MODEL = "gemini-2.5-flash"
DEFAULT_AI_TIMEOUT_SECONDS = 20.0


class Settings(BaseSettings):
    """Resolved settings for one invocation.

    Settings are read per request on purpose: deployments and tests can change
    the environment without restarting the process. Process environment
    variables take precedence over values found in the repository `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="AUDITOR_",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    signing_key: str | None = None
    approval_threshold: int = DEFAULT_APPROVAL_THRESHOLD
    trace_path: Path = DEFAULT_TRACE_PATH
    max_caption_chars: int = 2200
    max_hashtags: int = 30
    allowed_image_formats: tuple[str, ...] = ("jpg", "jpeg", "png", "webp")

    # `validation_alias` is intentionally used so the credential keeps the
    # documented `AI_KEY` name instead of inheriting the AUDITOR_ prefix.
    ai_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_KEY", "AUDITOR_AI_KEY"),
    )
    ai_enabled: bool = False
    ai_model: str = DEFAULT_AI_MODEL
    ai_timeout_seconds: float = DEFAULT_AI_TIMEOUT_SECONDS

    @field_validator("signing_key", mode="before")
    @classmethod
    def _blank_signing_key_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("ai_key", mode="before")
    @classmethod
    def _blank_ai_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("approval_threshold", mode="before")
    @classmethod
    def _threshold_or_default(cls, value: object) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return DEFAULT_APPROVAL_THRESHOLD
        return parsed if 0 <= parsed <= 100 else DEFAULT_APPROVAL_THRESHOLD

    @property
    def using_development_key(self) -> bool:
        return not self.signing_key

    @property
    def resolved_signing_key(self) -> str:
        return self.signing_key or DEVELOPMENT_SIGNING_KEY


def load_settings() -> Settings:
    """Read environment variables and the repository `.env` file."""

    return Settings()
