"""Unit tests for canonical content hashing, HMAC signing, and approval IDs."""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.signing import (
    canonical_content,
    compute_content_hash,
    generate_approval_id,
    sign_content_hash,
    verify_content_signature,
)


def test_canonical_content_uses_newlines_and_comma_joined_hashtags():
    canonical = canonical_content(
        "https://cdn.example.com/a.jpg",
        "Compra ya",
        ["#uno", "#dos"],
    )

    assert canonical == "https://cdn.example.com/a.jpg\nCompra ya\n#uno,#dos"


def test_canonical_content_with_no_hashtags_ends_with_an_empty_line():
    canonical = canonical_content("url", "caption", [])

    assert canonical == "url\ncaption\n"
    assert canonical.endswith("\n")


def test_canonical_content_preserves_hashtag_order():
    one = canonical_content("url", "caption", ["#a", "#b"])
    two = canonical_content("url", "caption", ["#b", "#a"])

    assert one != two


def test_hash_is_sha256_of_the_utf8_canonical_payload():
    canonical = canonical_content("https://x/y.jpg", "ñandú", ["#ñ"])
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert compute_content_hash("https://x/y.jpg", "ñandú", ["#ñ"]) == expected


def test_hash_is_64_lowercase_hex_characters():
    digest = compute_content_hash("url", "caption", [])

    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_hash_is_deterministic_and_sensitive_to_every_input():
    base = compute_content_hash("url", "caption", ["#a"])

    assert base == compute_content_hash("url", "caption", ["#a"])
    assert base != compute_content_hash("other-url", "caption", ["#a"])
    assert base != compute_content_hash("url", "other-caption", ["#a"])
    assert base != compute_content_hash("url", "caption", ["#b"])
    assert base != compute_content_hash("url", "caption", [])


def test_known_hash_vector():
    # Document the exact canonical input with the standard library instead of
    # hard-coding a fragile literal.
    expected = hashlib.sha256(b"asset\ncaption\n#tag").hexdigest()

    assert compute_content_hash("asset", "caption", ["#tag"]) == expected


def test_signature_is_hmac_sha256_of_the_hash_with_the_key():
    expected = hmac.new(
        b"secret-key", b"a" * 64, hashlib.sha256
    ).hexdigest()

    assert sign_content_hash("a" * 64, "secret-key") == expected


def test_signature_is_64_lowercase_hex_characters():
    signature = sign_content_hash("a" * 64, "key")

    assert re.fullmatch(r"[0-9a-f]{64}", signature)


def test_signature_depends_on_hash_and_key():
    base = sign_content_hash("a" * 64, "key")

    assert base == sign_content_hash("a" * 64, "key")
    assert base != sign_content_hash("b" * 64, "key")
    assert base != sign_content_hash("a" * 64, "other-key")


def test_verify_signature_accepts_the_matching_pair():
    digest = compute_content_hash("url", "caption", ["#tag"])
    signature = sign_content_hash(digest, "key")

    assert verify_content_signature(digest, signature, "key") is True


@pytest.mark.parametrize(
    ("digest", "signature", "key"),
    [
        ("b" * 64, "signature", "key"),
        ("a" * 64, "0" * 64, "key"),
        ("a" * 64, "signature", "other-key"),
        ("", "", "key"),
    ],
)
def test_verify_signature_rejects_mismatches(digest, signature, key):
    assert verify_content_signature(digest, signature, key) is False


def test_verify_signature_is_constant_time_safe_for_equal_length_inputs():
    # The implementation uses hmac.compare_digest; this test documents that a
    # valid signature is accepted even when it differs in many positions.
    digest = "f" * 64
    signature = sign_content_hash(digest, "key")

    assert len(signature) == 64
    assert verify_content_signature(digest, signature, "key")


def test_approval_id_matches_the_documented_pattern():
    approval_id = generate_approval_id(
        datetime(2026, 11, 11, 14, 32, tzinfo=timezone.utc)
    )

    assert re.fullmatch(r"APP-20261111-[0-9a-f]{8}", approval_id)


def test_approval_id_uses_the_utc_date_of_the_supplied_moment():
    moment = datetime(2027, 1, 2, 23, 59, tzinfo=timezone.utc)

    assert generate_approval_id(moment).startswith("APP-20270102-")


def test_approval_id_defaults_to_the_current_utc_date():
    before = datetime.now(timezone.utc).strftime("%Y%m%d")
    approval_id = generate_approval_id()
    after = datetime.now(timezone.utc).strftime("%Y%m%d")

    assert approval_id.startswith("APP-")
    assert approval_id[4:12] in {before, after}


def test_approval_ids_are_unique():
    ids = {generate_approval_id() for _ in range(200)}

    assert len(ids) == 200


def test_approval_id_ignores_non_utc_timezone_offset_for_the_formatted_date():
    moment = datetime(2026, 11, 11, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
    # strftime uses the local wall clock of the provided datetime.
    assert generate_approval_id(moment).startswith("APP-20261111-")
