"""Approval identifiers, content hashing, and HMAC signing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone


def canonical_content(asset_url: str, caption: str, hashtags: list[str]) -> str:
    """Canonical byte sequence signed by AG-AUD and recomputed by AG-PUB."""

    return f"{asset_url}\n{caption}\n{','.join(hashtags)}"


def compute_content_hash(asset_url: str, caption: str, hashtags: list[str]) -> str:
    payload = canonical_content(asset_url, caption, hashtags).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sign_content_hash(content_hash: str, signing_key: str) -> str:
    return hmac.new(
        signing_key.encode("utf-8"),
        content_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_content_signature(content_hash: str, signature: str, signing_key: str) -> bool:
    expected = sign_content_hash(content_hash, signing_key)
    return hmac.compare_digest(expected, signature)


def generate_approval_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return f"APP-{moment.strftime('%Y%m%d')}-{secrets.token_hex(4)}"
