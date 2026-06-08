"""Data sources catalog service.

A data source is a pointer to a directory on disk (or a remote URL, for
e.g. ARCO ERA5) that the app can use as either ground-truth observations
or model forecasts in a benchmark. Replaces the env-var-driven YAML registry
for runtime use; the YAML files still ship and seed an empty DB on first
launch so testdata works without configuration.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.settings import _DATASETS_YAML, _MODELS_YAML, _env_key, _env_value

Kind = Literal["obs", "model"]
Status = Literal["ready", "invalid"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
    if isinstance(d.get("region"), str):
        d["region"] = d["region"].strip().lower() or None
    return d


def _file_glob(pattern: str) -> str:
    return pattern.replace("{}", "*")


def _coverage_years(files: list[Path]) -> tuple[int | None, int | None]:
    years = []
    for file in files:
        match = re.search(r"(?:19|20)\d{2}", file.name)
        if match:
            years.append(int(match.group(0)))
    if not years:
        return None, None
    return min(years), max(years)


def _normalized_metadata(kind: Kind, metadata: dict, files: list[Path]) -> dict:
    normalized = dict(metadata)
    variable_key = "obs_var" if kind == "obs" else "model_var"
    pattern_key = "obs_file_pattern" if kind == "obs" else "file_pattern"
    default_variable = "RAINFALL" if kind == "obs" else "tp"
    normalized[variable_key] = str(
        normalized.get(variable_key) or default_variable
    ).strip()
    normalized[pattern_key] = str(normalized.get(pattern_key) or "{}.nc").strip()

    start_year, end_year = _coverage_years(files)
    if start_year is not None:
        normalized["start_year"] = start_year
        normalized["end_year"] = end_year

    if kind == "model":
        normalized.setdefault("model_type", "AIWP")
        normalized.setdefault("unit_cvt", 1.0)
        normalized.setdefault("probabilistic", False)
        normalized.setdefault("members", None)
        normalized.setdefault("init_days", "0")
        if start_year is not None and end_year is not None:
            normalized.setdefault("start_date", f"{start_year}-01-01")
            normalized.setdefault("end_date", f"{end_year}-12-31")
            normalized.setdefault("start_year_clim", start_year)
            normalized.setdefault("end_year_clim", end_year)
    return normalized


def _inspect_local_source(kind: Kind, path: str, metadata: dict) -> tuple[Status, str | None, dict]:
    directory = Path(path)
    if not directory.exists():
        return "invalid", "Directory does not exist.", metadata
    if not directory.is_dir():
        return "invalid", "Path is not a directory.", metadata
    try:
        next(directory.iterdir(), None)
    except PermissionError:
        return "invalid", "Directory is not readable.", metadata

    pattern_key = "obs_file_pattern" if kind == "obs" else "file_pattern"
    pattern = str(metadata.get(pattern_key) or "{}.nc").strip()
    files = sorted(file for file in directory.glob(_file_glob(pattern)) if file.is_file())
    if not files:
        return "invalid", f"No files match {pattern!r}.", metadata

    normalized = _normalized_metadata(kind, metadata, files)
    variable_key = "obs_var" if kind == "obs" else "model_var"
    variable = normalized[variable_key]
    try:
        import xarray as xr

        with xr.open_dataset(files[0]) as dataset:
            available = sorted(dataset.data_vars)
    except Exception as exc:
        return (
            "invalid",
            f"Could not open {files[0].name} as NetCDF: {type(exc).__name__}: {exc}",
            normalized,
        )
    if variable not in available:
        names = ", ".join(available[:8]) or "none"
        return (
            "invalid",
            f"Variable {variable!r} was not found in {files[0].name}. Available variables: {names}.",
            normalized,
        )
    if kind == "model" and normalized.get("start_year") is None:
        return (
            "invalid",
            "Could not infer model coverage years from matching filenames.",
            normalized,
        )
    return "ready", None, normalized


async def validate_source(kind: Kind, path: str, metadata: dict) -> tuple[Status, str | None, dict]:
    return await asyncio.to_thread(_inspect_local_source, kind, path, metadata)


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
    normalized_region = region.strip().lower() if region else None
    source_id = str(uuid.uuid4())
    status, validation_error, normalized_metadata = await validate_source(
        kind, path, metadata or {}
    )
    now = _now()
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, :region, :metadata, "
                "'local_directory', :status, :validation_error, :now, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": path,
                "region": normalized_region,
                # SQLite's JSON column doesn't auto-serialize Python dicts; emit a string.
                "metadata": json.dumps(normalized_metadata),
                "status": status,
                "validation_error": validation_error,
                "now": now,
            },
        )
        result = await conn.execute(
            text("SELECT * FROM data_sources WHERE id = :id"), {"id": source_id}
        )
        return _row_dict(result.mappings().fetchone())


async def update_source(
    source_id: str,
    *,
    name: str,
    path: str,
    region: str | None,
    metadata: dict,
) -> dict | None:
    normalized_region = region.strip().lower() if region else None
    async with get_db() as conn:
        existing = (
            (
                await conn.execute(
                    text("SELECT kind FROM data_sources WHERE id = :id"),
                    {"id": source_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not existing:
        return None

    kind: Kind = existing["kind"]
    status, validation_error, normalized_metadata = await validate_source(
        kind, path, metadata
    )
    async with get_db() as conn:
        result = await conn.execute(
            text(
                "UPDATE data_sources SET name = :name, path = :path, region = :region, "
                "metadata = :metadata, status = :status, validation_error = :error, "
                "updated_at = :now WHERE id = :id RETURNING *"
            ),
            {
                "id": source_id,
                "name": name,
                "path": path,
                "region": normalized_region,
                "metadata": json.dumps(normalized_metadata),
                "status": status,
                "error": validation_error,
                "now": _now(),
            },
        )
        row = result.mappings().fetchone()
        return _row_dict(row) if row else None


async def revalidate_source(source_id: str) -> dict | None:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM data_sources WHERE id = :id"),
                    {"id": source_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        return None
    source = _row_dict(row)
    return await update_source(
        source_id,
        name=source["name"],
        path=source["path"],
        region=source.get("region"),
        metadata=source["metadata"],
    )


async def get_source(source_id: str) -> dict | None:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM data_sources WHERE id = :id"),
                    {"id": source_id},
                )
            )
            .mappings()
            .fetchone()
        )
    return _row_dict(row) if row else None


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
        rows = (
            (
                await conn.execute(
                    text("SELECT id FROM data_sources WHERE status != 'ready'")
                )
            )
            .mappings()
            .fetchall()
            if existing
            else []
        )
    if existing:
        for row in rows:
            await revalidate_source(row["id"])
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
                "obs_var": entry.get("obs_var", "RAINFALL"),
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
