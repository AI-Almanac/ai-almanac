"""Tests for the `ai-almanac backup` CLI command."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_almanac.cli import app


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    from ai_almanac.settings import reload_settings

    reload_settings()


def _create_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "almanac.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO test VALUES (42)")
    conn.commit()
    conn.close()
    return db_path


runner = CliRunner()


def test_backup_creates_db_file(tmp_path: Path) -> None:
    _create_test_db(tmp_path)
    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0, result.output

    backups = list((tmp_path / "backups").glob("almanac-*.db"))
    assert len(backups) == 1

    # Verify integrity
    conn = sqlite3.connect(str(backups[0]))
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    conn.close()
    assert rows[0][0] == "ok"


def test_backup_copies_config_yaml(tmp_path: Path) -> None:
    _create_test_db(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("llm_model: test\n")

    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0

    configs = list((tmp_path / "backups").glob("config-*.yaml"))
    assert len(configs) == 1
    assert configs[0].read_text() == "llm_model: test\n"


def test_backup_copies_secrets_env_with_0600(tmp_path: Path) -> None:
    _create_test_db(tmp_path)
    from ai_almanac.paths import secrets_env_path

    s = secrets_env_path()
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text("CREDENTIAL_ENCRYPTION_KEY=abc\n")
    s.chmod(0o600)

    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0

    import stat

    secrets_backups = list((tmp_path / "backups").glob("secrets-*.env"))
    assert len(secrets_backups) == 1
    mode = secrets_backups[0].stat().st_mode
    assert not (mode & (stat.S_IRGRP | stat.S_IROTH))


def test_backup_skips_optional_files_when_absent(tmp_path: Path) -> None:
    _create_test_db(tmp_path)
    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0
    assert "config" not in result.output
    assert "secrets" not in result.output


def test_backup_custom_dest(tmp_path: Path) -> None:
    _create_test_db(tmp_path)
    dest = tmp_path / "my-backups"
    result = runner.invoke(app, ["backup", "--dest", str(dest)])
    assert result.exit_code == 0
    assert dest.exists()
    assert list(dest.glob("almanac-*.db"))


def test_backup_fails_for_non_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch resolve_database_url to return a postgres URL without touching env
    # (env-var changes leak through reload_settings() into subsequent tests).
    from ai_almanac.settings import Settings

    monkeypatch.setattr(
        Settings,
        "resolve_database_url",
        lambda self: "postgresql+asyncpg://user:pass@localhost/db",
    )
    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 1
    assert "pg_dump" in result.output or "pg_dump" in (result.stderr or "")
