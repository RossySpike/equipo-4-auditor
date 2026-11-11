"""Settings resolution: environment, `.env`, aliases, and defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    DEFAULT_AI_MODEL,
    DEFAULT_AI_TIMEOUT_SECONDS,
    DEFAULT_APPROVAL_THRESHOLD,
    DEFAULT_TRACE_PATH,
    DEVELOPMENT_SIGNING_KEY,
    PROJECT_ROOT,
    Settings,
    load_settings,
)


@pytest.fixture
def env(monkeypatch):
    """Give each test a clean AUDITOR_* / AI_KEY environment."""

    for name in (
        "AI_KEY",
        "AUDITOR_AI_KEY",
        "AUDITOR_AI_ENABLED",
        "AUDITOR_AI_MODEL",
        "AUDITOR_AI_TIMEOUT_SECONDS",
        "AUDITOR_SIGNING_KEY",
        "AUDITOR_APPROVAL_THRESHOLD",
        "AUDITOR_TRACE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_settings(env, **overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_documented_defaults_when_nothing_is_configured(env):
    settings = make_settings(env)

    assert settings.signing_key is None
    assert settings.using_development_key is True
    assert settings.resolved_signing_key == DEVELOPMENT_SIGNING_KEY
    assert settings.approval_threshold == DEFAULT_APPROVAL_THRESHOLD == 85
    assert settings.trace_path == DEFAULT_TRACE_PATH
    assert settings.max_caption_chars == 2200
    assert settings.max_hashtags == 30
    assert settings.allowed_image_formats == ("jpg", "jpeg", "png", "webp")
    assert settings.ai_key is None
    assert settings.ai_enabled is False
    assert settings.ai_model == DEFAULT_AI_MODEL == "gemini-2.5-flash"
    assert settings.ai_timeout_seconds == DEFAULT_AI_TIMEOUT_SECONDS == 20.0


def test_project_root_points_to_the_repository():
    assert PROJECT_ROOT == Path(__file__).resolve().parent.parent
    assert (PROJECT_ROOT / "app" / "config.py").is_file()


def test_reads_settings_from_an_explicit_env_file(env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'AI_KEY="secret-from-file"',
                "AUDITOR_AI_ENABLED=true",
                "AUDITOR_AI_MODEL=gemini-test-model",
                "AUDITOR_AI_TIMEOUT_SECONDS=7.5",
                "AUDITOR_SIGNING_KEY=file-signing-key",
                "AUDITOR_APPROVAL_THRESHOLD=91",
                "AUDITOR_TRACE_PATH=logs/file-trace.jsonl",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.ai_key is not None
    assert settings.ai_key.get_secret_value() == "secret-from-file"
    assert settings.ai_enabled is True
    assert settings.ai_model == "gemini-test-model"
    assert settings.ai_timeout_seconds == 7.5
    assert settings.signing_key == "file-signing-key"
    assert settings.using_development_key is False
    assert settings.resolved_signing_key == "file-signing-key"
    assert settings.approval_threshold == 91
    assert settings.trace_path == Path("logs/file-trace.jsonl")


def test_process_environment_overrides_env_file(env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AI_KEY=file-key\nAUDITOR_SIGNING_KEY=file-signing\nAUDITOR_APPROVAL_THRESHOLD=10\n",
        encoding="utf-8",
    )
    env.setenv("AI_KEY", "process-key")
    env.setenv("AUDITOR_SIGNING_KEY", "process-signing")
    env.setenv("AUDITOR_APPROVAL_THRESHOLD", "99")

    settings = Settings(_env_file=env_file)

    assert settings.ai_key is not None
    assert settings.ai_key.get_secret_value() == "process-key"
    assert settings.signing_key == "process-signing"
    assert settings.approval_threshold == 99


def test_auditor_ai_key_alias_is_also_accepted(env):
    env.setenv("AUDITOR_AI_KEY", "alias-key")

    settings = make_settings(env)

    assert settings.ai_key is not None
    assert settings.ai_key.get_secret_value() == "alias-key"


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n", "   \t  "])
def test_blank_ai_key_resolves_to_none(env, blank):
    env.setenv("AI_KEY", blank)

    settings = make_settings(env)

    assert settings.ai_key is None


def test_blank_ai_key_in_env_file_resolves_to_none(env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('AI_KEY="   "\n', encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.ai_key is None


def test_secret_value_is_hidden_in_repr_and_str(env):
    env.setenv("AI_KEY", "super-secret-value-never-print")

    settings = make_settings(env)

    assert "super-secret-value-never-print" not in repr(settings)
    assert "super-secret-value-never-print" not in str(settings)
    assert "**********" in repr(settings)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("1", 1),
        ("85", 85),
        ("100", 100),
        ("-1", DEFAULT_APPROVAL_THRESHOLD),
        ("101", DEFAULT_APPROVAL_THRESHOLD),
        ("not-a-number", DEFAULT_APPROVAL_THRESHOLD),
        ("85.5", DEFAULT_APPROVAL_THRESHOLD),
        ("", DEFAULT_APPROVAL_THRESHOLD),
    ],
)
def test_approval_threshold_validation_and_fallback(env, raw, expected):
    env.setenv("AUDITOR_APPROVAL_THRESHOLD", raw)

    assert make_settings(env).approval_threshold == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("TRUE", True), ("1", True), ("yes", True),
     ("false", False), ("FALSE", False), ("0", False), ("no", False)],
)
def test_boolean_ai_enabled_parsing(env, raw, expected):
    env.setenv("AUDITOR_AI_ENABLED", raw)

    assert make_settings(env).ai_enabled is expected


def test_invalid_boolean_ai_enabled_is_a_validation_error(env):
    env.setenv("AUDITOR_AI_ENABLED", "maybe")

    with pytest.raises(ValidationError):
        make_settings(env)


def test_timeout_accepts_float_and_integer_strings(env):
    env.setenv("AUDITOR_AI_TIMEOUT_SECONDS", "3.25")
    assert make_settings(env).ai_timeout_seconds == 3.25

    env.setenv("AUDITOR_AI_TIMEOUT_SECONDS", "9")
    assert make_settings(env).ai_timeout_seconds == 9.0


def test_trace_path_is_coerced_to_path(env):
    env.setenv("AUDITOR_TRACE_PATH", "custom/traces/audit.jsonl")

    assert make_settings(env).trace_path == Path("custom/traces/audit.jsonl")


def test_blank_signing_key_uses_the_development_key(env):
    env.setenv("AUDITOR_SIGNING_KEY", "   ")

    settings = make_settings(env)

    assert settings.using_development_key is True
    assert settings.resolved_signing_key == DEVELOPMENT_SIGNING_KEY


def test_settings_are_frozen(env):
    settings = make_settings(env)

    with pytest.raises(ValidationError):
        settings.approval_threshold = 10  # type: ignore[misc]


def test_unknown_environment_variables_are_ignored(env):
    env.setenv("AUDITOR_UNKNOWN_SETTING", "value")

    settings = make_settings(env)

    assert not hasattr(settings, "unknown_setting")


def test_load_settings_reads_the_process_environment(env):
    env.setenv("AUDITOR_SIGNING_KEY", "loaded-key")
    env.setenv("AUDITOR_APPROVAL_THRESHOLD", "77")

    settings = load_settings()

    assert isinstance(settings, Settings)
    assert settings.resolved_signing_key == "loaded-key"
    assert settings.approval_threshold == 77


def test_load_settings_uses_the_repository_env_file_without_exposing_it(env):
    settings = load_settings()

    # The repository `.env` may contain a real AI_KEY; it must only be exposed
    # through SecretStr and never through its repr.
    assert settings.ai_key is None or isinstance(settings.ai_key.get_secret_value(), str)
    if settings.ai_key is not None:
        assert settings.ai_key.get_secret_value() not in repr(settings)
