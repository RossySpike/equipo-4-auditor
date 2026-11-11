"""Runtime configuration for the AG-AUD auditor service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEVELOPMENT_SIGNING_KEY = "dev-insecure-auditor-signing-key"
DEFAULT_APPROVAL_THRESHOLD = 85
DEFAULT_TRACE_PATH = Path("data/audit_trace.jsonl")


@dataclass(frozen=True)
class Settings:
    """Resolved settings for one invocation.

    Settings are read per request on purpose: deployments and tests can change
    the environment without restarting the process.
    """

    signing_key: str
    using_development_key: bool
    approval_threshold: int
    trace_path: Path
    max_caption_chars: int = 2200
    max_hashtags: int = 30
    allowed_image_formats: tuple[str, ...] = ("jpg", "jpeg", "png", "webp")


def _read_threshold() -> int:
    raw = os.getenv("AUDITOR_APPROVAL_THRESHOLD", str(DEFAULT_APPROVAL_THRESHOLD))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_APPROVAL_THRESHOLD
    return value if 0 <= value <= 100 else DEFAULT_APPROVAL_THRESHOLD


def load_settings() -> Settings:
    """Read environment variables and return immutable settings."""

    key = os.getenv("AUDITOR_SIGNING_KEY")
    trace_path = os.getenv("AUDITOR_TRACE_PATH")
    return Settings(
        signing_key=key or DEVELOPMENT_SIGNING_KEY,
        using_development_key=not bool(key),
        approval_threshold=_read_threshold(),
        trace_path=Path(trace_path) if trace_path else DEFAULT_TRACE_PATH,
    )
