"""Backfill ``init_month_days`` on already-registered model data sources.

The live forecast season loop now selects issue dates from each source's fixed
calendar schedule (``init_month_days``, a list of ``MM-DD``) instead of an
inferred weekday grid, because the archives pin issue dates to calendar dates
whose weekday drifts year to year (see forecast_pipeline.season_issue_dates).
Sources registered before that change have no schedule, so they fall back to
the old weekday behavior and miss the shared trajectory cache across regions.

This re-runs inspection for each model source, which reopens its data and
recomputes the metadata (now including ``init_month_days``). It is idempotent
and DB-agnostic (SQLite or Postgres) — it goes through the same service code as
registration rather than editing JSON by hand. Run it once, in the environment
whose DB and data you want to fix, AFTER deploying the calendar-schedule change.

Usage:
    pixi run python scripts/backfill_init_month_days.py             # all model sources
    pixi run python scripts/backfill_init_month_days.py <id> <id>   # only these ids
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services import data_sources as ds


async def _model_source_ids() -> list[tuple[str, str]]:
    async with get_db() as conn:
        rows = (
            (await conn.execute(text("SELECT id, name FROM data_sources WHERE kind = 'model'")))
            .mappings()
            .all()
        )
    return [(r["id"], r["name"]) for r in rows]


async def main(only_ids: list[str]) -> None:
    sources = await _model_source_ids()
    if only_ids:
        wanted = set(only_ids)
        sources = [(sid, name) for sid, name in sources if sid in wanted]
    if not sources:
        print("No matching model sources found.")
        return

    for source_id, name in sources:
        before = await ds.get_source(source_id)
        before_schedule = (before or {}).get("metadata", {}).get("init_month_days")
        try:
            updated = await ds.revalidate_source(source_id)
        except Exception as exc:  # keep going; one bad source shouldn't abort the run
            print(f"FAILED  {name} ({source_id}): {type(exc).__name__}: {exc}")
            continue
        after_schedule = (updated or {}).get("metadata", {}).get("init_month_days")
        status = (updated or {}).get("status")
        n_before = len(before_schedule) if before_schedule else 0
        n_after = len(after_schedule) if after_schedule else 0
        print(
            f"OK      {name} ({source_id}): "
            f"init_month_days {n_before} -> {n_after} dates, status={status}"
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
