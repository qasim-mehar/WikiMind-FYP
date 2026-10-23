from __future__ import annotations

import hashlib
import os
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from wikimind.config import Settings, load_dotenv, redact, redact_secret
from wikimind.db.connection import with_dbname
from wikimind.db.ids import CHANNEL_ID_SEPARATOR, channel_id


def test_redact_masks_dsn_password() -> None:
    result = redact("postgresql://alice:hunter2@localhost:5432/wiki")
    assert result == "postgresql://alice:***@localhost:5432/wiki"
    assert "hunter2" not in result


def test_redact_masks_inline_and_bearer_secrets() -> None:
    assert "abc" not in redact("password=abc&host=x")
    assert "tok" not in redact("Authorization: Bearer tok123")


def test_settings_repr_is_redacted() -> None:
    settings = Settings(
        db_dsn="postgresql://alice:hunter2@localhost/wiki",
        gemini_api_key="AIza-secret-value",
        openrouter_api_key="sk-or-secret",
    )
    rendered = repr(settings)
    assert "hunter2" not in rendered
    assert "AIza-secret-value" not in rendered
    assert "sk-or-secret" not in rendered
    assert "***" in rendered


def test_redact_secret_keeps_short_suffix() -> None:
    assert redact_secret("abcdefgh", keep=4) == "***efgh"
    assert redact_secret("abc", keep=4) == "***"


def test_load_dotenv_does_not_override_by_default(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport WIKIMIND_DB='postgresql://example/db'\nGEMINI_API_KEY=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WIKIMIND_DB", "postgresql://existing/db")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert load_dotenv(env_file) == env_file
    assert "existing" in os.environ["WIKIMIND_DB"]
    assert os.environ["GEMINI_API_KEY"] == "from-file"


def test_channel_id_contract() -> None:
    expected = hashlib.sha256(f"paragraph{CHANNEL_ID_SEPARATOR}Hello world".encode()).hexdigest()
    assert channel_id("paragraph", "Hello world") == expected
    assert len(expected) == 64


def test_with_dbname_preserves_connection_parameters() -> None:
    dsn = with_dbname("postgresql://alice:pw@localhost:5432/first", "second")
    params = conninfo_to_dict(dsn)
    assert params["dbname"] == "second"
    assert params["user"] == "alice"
    assert params["host"] == "localhost"
