"""Data sources catalog service.

A data source is a pointer to a directory on disk (or a remote URL, for
e.g. ARCO ERA5) that the app can use as either ground-truth observations
or model forecasts in a benchmark. Replaces the env-var-driven YAML registry
for runtime use; the YAML files still ship and seed an empty DB on first
launch so testdata works without configuration.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

import yaml
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.settings import _DATASETS_YAML, _MODELS_YAML, _env_value, _env_key

Kind = Literal["obs", "model"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_metadata(value) -> dict:
    """SQLite returns JSON columns as strings; deserialize for callers."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value or {}


def _row_dict(row) -> dict:
    d = dict(row)
    d["metadata"] = _decode_metadata(d.get("metadata"))
    return d


async def list_sources(kind: Kind | None = None) -> list[dict]:
    """Return all data sources, optionally filtered by kind."""
    async with get_db() as conn:
        if kind is None:
            result = await conn.execute(
                text("SELECT * FROM data_sources ORDER BY kind, name")
            )
        else:
            result = await conn.execute(
                text("SELECT * FROM data_sources WHERE kind = :kind ORDER BY name"),
                {"kind": kind},
            )
        return [_row_dict(row) for row in result.mappings().fetchall()]


async def create_source(
    kind: Kind,
    name: str,
    path: str,
    region: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Insert a new data source. Returns the created row."""
    source_id = str(uuid.uuid4())
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources (id, kind, name, path, region, metadata, created_at) "
                "VALUES (:id, :kind, :name, :path, :region, :metadata, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": path,
                "region": region,
                # SQLite's JSON column doesn't auto-serialize Python dicts; emit a string.
                "metadata": json.dumps(metadata or {}),
                "now": _now(),
            },
        )
        result = await conn.execute(
            text("SELECT * FROM data_sources WHERE id = :id"), {"id": source_id}
        )
        return _row_dict(result.mappings().fetchone())


async def delete_source(source_id: str) -> bool:
    async with get_db() as conn:
        result = await conn.execute(
            text("DELETE FROM data_sources WHERE id = :id"), {"id": source_id}
        )
        return result.rowcount > 0


async def get_obs_sources() -> list[dict]:
    """Return all configured obs data sources (for the demo-dataset registry)."""
    return await list_sources(kind="obs")


async def get_model_sources(region: str | None = None) -> list[dict]:
    """Return all configured model data sources, optionally for a specific region."""
    sources = await list_sources(kind="model")
    if region:
        sources = [s for s in sources if (s.get("region") or "").lower() == region.lower()]
    return sources


# ---------------------------------------------------------------------------
# First-launch seeder.
# ---------------------------------------------------------------------------


async def seed_from_yaml_if_empty() -> int:
    """Populate the data_sources table from the packaged YAMLs on first launch.

    Idempotent: skips if the table already has any rows. Honors env vars from
    the previous registry (e.g. `TEST_ETHIOPIA_OBS_DIR`) so existing testdata
    setups continue to work without changes.

    Returns the number of rows inserted (0 if already seeded).
    """
    async with get_db() as conn:
        existing = (
            await conn.execute(text("SELECT COUNT(*) FROM data_sources"))
        ).scalar()
        if existing and existing > 0:
            return 0

    inserted = 0

    # Seed obs datasets from datasets.yaml.
    raw_datasets = yaml.safe_load(_DATASETS_YAML.read_text())
    for entry in raw_datasets:
        provider = entry.get("provider", "local")
        if provider != "local":
            # Remote (ARCO/e2s) sources don't fit the local-path model; skip
            # for the POC. We'll handle remote registration separately later.
            continue
        env_key = _env_key(entry["id"], "obs_dir")
        path = _env_value(env_key)
        if not path:
            continue
        await create_source(
            kind="obs",
            name=entry.get("name", entry["id"]),
            path=path,
            region=entry.get("region"),
            metadata={
                "yaml_id": entry["id"],
                "obs_file_pattern": entry.get("obs_file_pattern", "{}.nc"),
            },
        )
        inserted += 1

    # Seed model directories from models.yaml.
    raw_models = yaml.safe_load(_MODELS_YAML.read_text())
    for entry in raw_models:
        env_key = _env_key(entry["region"], entry["id"], "model_dir")
        path = _env_value(env_key)
        if not path:
            continue
        meta = {k: v for k, v in entry.items() if k not in ("display_name", "region")}
        meta["yaml_id"] = entry["id"]  # preserve the slug so existing UI URLs work
        await create_source(
            kind="model",
            name=entry.get("display_name", entry["id"]),
            path=path,
            region=entry["region"],
            metadata=meta,
        )
        inserted += 1

    return inserted
