"""Rows written straight into the database, for tests that need a job or a data source
without going through submission or registration."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from ai_almanac.paths import database_path

BENCHMARK_CONFIG = {
    "model_name": "AIFS",
    "model_display_name": "AIFS",
    "obs_dir": "/data/ethiopia/obs",
    "model_config": {"model_dir": "/data/ethiopia/aifs", "start_year": 1998, "end_year": 2024},
    "region_id": "ethiopia",
    "region_name": "Ethiopia",
    "dataset_config": {"source_name": "CHIRPS Ethiopia", "bucket": "gs://almanac/obs"},
    "romp_params": {
        "start_date": "1998-05-02",
        "end_date": "2024-07-29",
        "max_forecast_day": 30,
    },
    "model_files": {"aifs": ["/data/aifs/2008.nc", "/data/aifs/2009.nc"]},
    "storage_key": "abc",
}


def benchmark_config(years: tuple[int, int] = (1998, 2024), **romp_params: object) -> dict:
    """An AIFS benchmark over Ethiopia scoring onsets in ``years``."""
    return {
        "model_name": "AIFS",
        "region_id": "ethiopia",
        "region_name": "Ethiopia",
        "romp_params": {
            "start_date": f"{years[0]}-05-02",
            "end_date": f"{years[1]}-07-29",
            **romp_params,
        },
    }


def ran_for(minutes: int | None) -> dict:
    """Job timestamps for a run that started an hour after it was queued and took
    ``minutes``, or is still going when ``minutes`` is None."""
    completed = None if minutes is None else f"2026-01-01T01:{minutes:02d}:00"
    return {"started_at": "2026-01-01T01:00:00", "completed_at": completed}


def insert_job(
    user_id: str,
    config: dict,
    *,
    job_type: str = "benchmark",
    status: str = "complete",
    created_at: str = "2026-01-01T00:00:00",
    **columns: object,
) -> str:
    """A job row as the job manager would leave it; ``columns`` sets any other column."""
    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "dataset_id": "ds",
        "status": status,
        "job_type": job_type,
        "config_json": json.dumps(config),
        "created_at": created_at,
        **columns,
    }
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with sqlite3.connect(database_path()) as conn:
        conn.execute(f"INSERT INTO jobs ({names}) VALUES ({marks})", tuple(row.values()))
        conn.commit()
    return row["id"]


async def seed_source(
    kind: str,
    name: str,
    path: str | None = None,
    years: tuple[int, int] | None = None,
    grid_step: float | None = None,
    extra_metadata: dict | None = None,
    *,
    region: str = "india",
) -> str:
    """A ready gs:// data source, registered without checking the bucket."""
    from ai_almanac.server.db import get_db

    source_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    metadata = {"start_year": years[0], "end_year": years[1]} if years else {}
    if grid_step is not None:
        metadata["grid_step_deg"] = grid_step
    metadata.update(extra_metadata or {})
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, :region, :metadata, 'gcs', 'ready', "
                "NULL, :now, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": path or f"gs://data/{kind}/{name}",
                "region": region,
                "metadata": json.dumps(metadata),
                "now": now,
            },
        )
    return source_id


async def link_forecast_model(source_id: str, forecast_model_id: str) -> None:
    from ai_almanac.server.db import get_db
    from ai_almanac.server.services import data_sources

    source = await data_sources.get_source(source_id)
    metadata = {**source["metadata"], "forecast_model_id": forecast_model_id}
    async with get_db() as conn:
        await conn.execute(
            text("UPDATE data_sources SET metadata = :metadata WHERE id = :id"),
            {"metadata": json.dumps(metadata), "id": source_id},
        )
