"""Trajectory-set generation tracking.

A trajectory *set* is the deterministic season rollout for one
`(model_name, init_source, season)` triple. It is model-scoped and
region-independent (native grid, reduction applied downstream), so a single
set serves every blend and region that uses the model. This service records
generation status and which init dates have been rolled out and cached, so a
blend forecast can check readiness before it runs and an admin can see
coverage.

Backed by the repurposed `forecast_runs` table (migration 0016), one row per
set. Async, taking an `AsyncConnection` like the rest of the server services.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ai_almanac.server.tables import forecast_runs

Status = Literal["pending", "running", "complete", "failed"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso(value: date | str) -> str:
    """Canonical ISO date string. Parse at the boundary so the covered-set
    comparison never mixes date objects with differently-formatted strings."""
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _covered(row) -> set[str]:
    raw = row["covered_init_dates"]
    if not raw:
        return set()
    if isinstance(raw, str):  # tolerate rows written as a JSON string
        raw = json.loads(raw)
    return {_iso(item) for item in raw}


async def get_set(
    conn: AsyncConnection, *, model_name: str, init_source: str, season: str
) -> dict | None:
    row = (
        await conn.execute(
            sa.select(forecast_runs).where(
                forecast_runs.c.model_name == model_name,
                forecast_runs.c.init_source == init_source,
                forecast_runs.c.season == season,
            )
        )
    ).mappings().fetchone()
    return dict(row) if row else None


async def create_or_get_set(
    conn: AsyncConnection,
    *,
    model_id: str,
    model_name: str,
    init_source: str,
    season: str,
    triggered_by_user_id: str,
    storage_prefix: str,
    config: dict | None = None,
) -> dict:
    """Idempotent on `(model_name, init_source, season)` — a set is a shared
    asset, so a second request for the same triple returns the existing row
    rather than starting a duplicate generation. The unique index backstops a
    race with a hard failure on the losing INSERT."""
    existing = await get_set(
        conn, model_name=model_name, init_source=init_source, season=season
    )
    if existing:
        return existing

    await conn.execute(
        sa.insert(forecast_runs).values(
            id=str(uuid.uuid4()),
            user_id=triggered_by_user_id,
            status="pending",
            model_id=model_id,
            model_name=model_name,
            init_source=init_source,
            season=season,
            init_time=None,
            variables=[],
            lead_hours=[],
            storage_prefix=storage_prefix,
            config_json=config,
            covered_init_dates=[],
            created_at=_now(),
        )
    )
    created = await get_set(
        conn, model_name=model_name, init_source=init_source, season=season
    )
    assert created is not None  # just inserted
    return created


async def mark_dates_covered(
    conn: AsyncConnection, set_id: str, dates: Iterable[date | str]
) -> None:
    """Union new init dates into the set's coverage (rollouts are additive —
    the incremental 'update' path only ever adds elapsed dates)."""
    row = (
        await conn.execute(
            sa.select(forecast_runs).where(forecast_runs.c.id == set_id)
        )
    ).mappings().fetchone()
    if row is None:
        raise ValueError(f"unknown trajectory set {set_id!r}")
    covered = _covered(row) | {_iso(d) for d in dates}
    await conn.execute(
        sa.update(forecast_runs)
        .where(forecast_runs.c.id == set_id)
        .values(covered_init_dates=sorted(covered))
    )


async def set_status(
    conn: AsyncConnection, set_id: str, status: Status, *, error: str | None = None
) -> None:
    values: dict = {"status": status, "error": error}
    if status == "running":
        values["started_at"] = _now()
    elif status in ("complete", "failed"):
        values["completed_at"] = _now()
    await conn.execute(
        sa.update(forecast_runs).where(forecast_runs.c.id == set_id).values(**values)
    )


async def set_is_ready(
    conn: AsyncConnection,
    *,
    model_name: str,
    init_source: str,
    season: str,
    needed_dates: Iterable[date | str],
) -> bool:
    """True iff the set exists, generation is complete, and every needed init
    date is covered. This is the gate a blend forecast checks before running."""
    row = await get_set(
        conn, model_name=model_name, init_source=init_source, season=season
    )
    if row is None or row["status"] != "complete":
        return False
    return {_iso(d) for d in needed_dates} <= _covered(row)


async def mark_coverage_from_config(conn: AsyncConnection, config: dict) -> None:
    """Mark every model in a completed forecast job's config as covered and its
    set complete. The async twin of job_workload._mark_trajectory_coverage, used
    by the Modal completion reconciler (the local subprocess path marks its own).
    """
    from ai_almanac.server.services.forecast_pipeline import season_covered_dates

    init_source = config.get("init_source") or "gfs"
    season = str(config.get("season") or datetime.now(UTC).year)
    covered = season_covered_dates(config)
    for name, dates in covered.items():
        row = await get_set(
            conn, model_name=name, init_source=init_source, season=season
        )
        if row is None:
            continue
        await mark_dates_covered(conn, row["id"], dates)
        await set_status(conn, row["id"], "complete")


async def list_sets(conn: AsyncConnection) -> list[dict]:
    """All trajectory sets, newest first — for the admin coverage view."""
    rows = (
        await conn.execute(
            sa.select(forecast_runs)
            .where(forecast_runs.c.season.isnot(None))
            .order_by(forecast_runs.c.created_at.desc())
        )
    ).mappings().all()
    return [dict(row) for row in rows]
