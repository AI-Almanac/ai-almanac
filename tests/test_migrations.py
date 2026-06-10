from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _alembic_config() -> Config:
    server_dir = Path(__file__).parents[1] / "src" / "ai_almanac" / "server"
    config = Config(str(server_dir / "alembic.ini"))
    config.set_main_option("script_location", str(server_dir / "alembic"))
    return config


def test_partial_ownership_migration_resumes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    config = _alembic_config()
    command.upgrade(config, "0005")

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'almanac.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE data_sources ADD COLUMN owner_id TEXT")
        connection.exec_driver_sql(
            "ALTER TABLE data_sources ADD COLUMN visibility TEXT NOT NULL DEFAULT 'shared'"
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'almanac.db'}")
    inspector = sa.inspect(engine)
    assert {
        "owner_id",
        "visibility",
        "origin",
    } <= {column["name"] for column in inspector.get_columns("data_sources")}
    assert {
        "visibility",
        "runner",
        "runner_handle",
        "artifacts_published_at",
    } <= {column["name"] for column in inspector.get_columns("jobs")}
    assert "display_name" in {column["name"] for column in inspector.get_columns("users")}
    engine.dispose()
