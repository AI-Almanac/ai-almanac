from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ai_almanac.server.database_urls import sync_database_url


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

    command.upgrade(config, "0006")

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'almanac.db'}")
    inspector = sa.inspect(engine)
    columns = {
        table: {c["name"] for c in inspector.get_columns(table)}
        for table in ("data_sources", "jobs", "users")
    }
    engine.dispose()
    assert {"owner_id", "visibility", "origin"} <= columns["data_sources"]
    assert {"visibility", "runner", "runner_handle"} <= columns["jobs"]
    assert "display_name" in columns["users"]

    command.upgrade(config, "head")


def test_reseeded_vestigial_regions_are_dropped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    config = _alembic_config()
    command.upgrade(config, "0027")

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'almanac.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO regions (id, display_name, description, land_only, shp_only, "
            "is_builtin, created_at, updated_at) VALUES "
            "('bangladesh', 'Bangladesh', '', 0, 0, 1, 'now', 'now'), "
            "('custom', 'Custom coverage', '', 0, 0, 1, 'now', 'now'), "
            "('ethiopia', 'Ethiopia', '', 0, 0, 1, 'now', 'now'), "
            "('horn', 'Horn', '', 0, 0, 0, 'now', 'now')"
        )
    engine.dispose()

    command.upgrade(config, "0028")

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'almanac.db'}")
    with engine.connect() as connection:
        region_ids = {row[0] for row in connection.exec_driver_sql("SELECT id FROM regions")}
    engine.dispose()
    assert region_ids == {"ethiopia", "horn"}


def test_sync_database_url_uses_psycopg3() -> None:
    url = "postgresql+asyncpg://almanac:secret@postgres/almanac"

    assert sync_database_url(url) == "postgresql+psycopg://almanac:secret@postgres/almanac"
