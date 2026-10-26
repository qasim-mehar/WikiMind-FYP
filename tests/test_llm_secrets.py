from __future__ import annotations

import logging
import os

import pytest

from wikimind.llm.errors import GeminiError
from wikimind.llm.secrets import (
    GEMINI_API_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
    get_key,
    load_dotenv,
    redact,
    redact_secrets,
    require_key,
)


def test_redact_exact_secret() -> None:
    assert redact("prefix sentinel-secret suffix", ["sentinel-secret"]) == (
        "prefix [REDACTED] suffix"
    )


def test_redact_google_key_shape() -> None:
    key = "AIza" + "A" * 30
    assert key not in redact(f"key={key}")


def test_redact_openrouter_key_shape() -> None:
    key = "sk-or-v1-" + "b" * 32
    assert key not in redact(f"key={key}")


def test_redact_bearer_token() -> None:
    assert redact("Authorization: Bearer abc.def-123") == (
        "Authorization: Bearer [REDACTED]"
    )
    assert redact("Bearer abc+/def==") == "Bearer [REDACTED]"


def test_redact_secrets_accepts_extra_values() -> None:
    assert redact_secrets("explicit-sentinel", ["explicit-sentinel"]) == ("[REDACTED]")


def test_redact_secrets_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "gemini-sentinel-123")
    monkeypatch.setenv(OPENROUTER_API_KEY_ENV, "openrouter-sentinel-456")
    text = "gemini-sentinel-123 and openrouter-sentinel-456"
    redacted = redact_secrets(text)
    assert "gemini-sentinel-123" not in redacted
    assert "openrouter-sentinel-456" not in redacted


def test_get_key_and_require_key(monkeypatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "  key-value  ")
    assert get_key(GEMINI_API_KEY_ENV) == "key-value"
    assert require_key(GEMINI_API_KEY_ENV) == "key-value"

    monkeypatch.delenv(OPENROUTER_API_KEY_ENV, raising=False)
    assert get_key(OPENROUTER_API_KEY_ENV) is None
    with pytest.raises(Exception, match="OPENROUTER_API_KEY"):
        require_key(OPENROUTER_API_KEY_ENV)


def test_load_dotenv_does_not_override(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "GEMINI_API_KEY=dotenv-gemini\n"
        "OPENROUTER_API_KEY='dotenv-openrouter'\n"
        "IGNORED LINE\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "already-set")
    monkeypatch.delenv(OPENROUTER_API_KEY_ENV, raising=False)

    assert load_dotenv(env_file) is True
    assert os.environ[GEMINI_API_KEY_ENV] == "already-set"
    assert os.environ[OPENROUTER_API_KEY_ENV] == "dotenv-openrouter"


def test_load_dotenv_missing_file(tmp_path) -> None:
    assert load_dotenv(tmp_path / "missing.env") is False


def test_load_dotenv_handles_bom_empty_env_and_inline_comments(
    monkeypatch, tmp_path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\ufeffGEMINI_API_KEY=dotenv-gemini # comment\n"
        "OPENROUTER_API_KEY=dotenv-openrouter\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "")
    monkeypatch.delenv(OPENROUTER_API_KEY_ENV, raising=False)

    assert load_dotenv(env_file) is True
    assert os.environ[GEMINI_API_KEY_ENV] == "dotenv-gemini"
    assert os.environ[OPENROUTER_API_KEY_ENV] == "dotenv-openrouter"


def test_keys_never_appear_in_logs_or_raised_errors(monkeypatch, caplog) -> None:
    key = "AIza" + "L" * 30
    monkeypatch.setenv(GEMINI_API_KEY_ENV, key)
    caplog.set_level(logging.DEBUG)

    with caplog.at_level(logging.DEBUG), pytest.raises(GeminiError) as excinfo:
        raise GeminiError(f"provider rejected key {key}")

    assert key not in caplog.text
    assert key not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)
