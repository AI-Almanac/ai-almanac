"""The workspace itself: which runs it holds, what waits on them, and its settings."""

from __future__ import annotations

from typing import Literal

import sqlalchemy as sa
from pydantic_ai import RunContext

from ai_almanac.server import tables

from .. import workspace_display, workspace_settings, workspaces
from ..focus_area import FocusArea
from ..seasons import SeasonWindow
from ..workspace_settings import WorkspaceSettings
from .registry import Refusal, primitive

FIND_RUNS_LIMIT = 15
RunKind = Literal["benchmark", "blend", "forecast"]


@primitive("workspace.find_runs", "1.0.0")
async def find_runs(
    ctx: RunContext,
    kind: RunKind | None = None,
    region_id: str | None = None,
    name: str | None = None,
    model: str | None = None,
) -> dict:
    """Find the user's own benchmark, blend and forecast runs from anywhere on the
    platform, newest first, so one they mention can be added here with `add_job`.

    Filter by kind, region, words from the run's name, or a model's name. Each run
    carries a `job_id` for tool calls only: never show it to the user or ask them for
    one. When several runs match, tell them apart by name, date, models and years.
    """
    jobs = tables.jobs
    rows = await workspaces.job_rows(jobs.c.user_id == ctx.deps.user_id)
    linked = {
        row["id"]
        for row in await workspaces.job_rows(
            sa.and_(jobs.c.user_id == ctx.deps.user_id, workspaces.in_workspace(ctx.deps.scope.key))
        )
    }
    runs = [_found_run(row, row["id"] in linked) for row in rows]
    matching = [run for run in runs if _run_matches(run, kind, region_id, name, model)]
    return {"runs": matching[:FIND_RUNS_LIMIT], "more": max(0, len(matching) - FIND_RUNS_LIMIT)}


def _found_run(row: dict, in_workspace: bool) -> dict:
    config = row["config"]
    params = config.get("blend_params") or {}
    return {
        "name": workspace_display.job_name(config) or "",
        "kind": row["job_type"] if row["job_type"] in ("blend", "forecast") else "benchmark",
        "region_id": config.get("region_id"),
        "models": config.get("model_names") or [m for m in [config.get("model_name")] if m],
        "training_years": params.get("training_years"),
        "cv_holdout_years": params.get("cv_holdout_years"),
        "status": row["status"],
        "submitted": row["created_at"],
        "finished": row["completed_at"],
        "in_workspace": in_workspace,
        "job_id": row["id"],
    }


def _run_matches(
    run: dict, kind: str | None, region_id: str | None, name: str | None, model: str | None
) -> bool:
    words = (name or "").lower().split()
    return (
        (kind is None or run["kind"] == kind)
        and (region_id is None or run["region_id"] == region_id)
        and all(word in run["name"].lower() for word in words)
        and (model is None or any(model.lower() in m.lower() for m in run["models"]))
    )


@primitive("workspace.add_job", "1.0.0")
async def add_job(ctx: RunContext, job_id: str) -> dict:
    """Link one of the user's existing benchmark, blend or forecast runs into this
    workspace so its outputs become artifacts here. Find the run with `find_runs`."""
    jobs = tables.jobs
    found = await workspaces.job_rows(
        sa.and_(jobs.c.id == job_id, jobs.c.user_id == ctx.deps.user_id)
    )
    if not found:
        raise Refusal(f"Job {job_id} not found")
    await workspaces.link_job(ctx.deps.scope.key, job_id, added_by=workspaces.ADDED_BY_ASSISTANT)
    return {"linked": job_id, "status": found[0]["status"]}


async def _updated_settings(ctx: RunContext, patch: WorkspaceSettings) -> dict:
    updated = await workspace_settings.update_settings(ctx.deps.scope.key, patch)
    return {
        "settings": updated.model_dump(mode="json"),
        "summary": workspace_settings.describe_settings(updated),
    }


@primitive("workspace.set_workspace_season", "1.0.0")
async def set_workspace_season(ctx: RunContext, season: SeasonWindow | None) -> dict:
    """Set the season this workspace works in, e.g. Ethiopia's Kiremt; null clears it.

    Settings are defaults for every later run here: a value the user gives in a request
    still overrides them, and runs already made keep the values they used. Call this
    when the user names their season or its dates, then tell them in a sentence what
    changed and that later runs will use it."""
    return await _updated_settings(ctx, WorkspaceSettings(season=season))


@primitive("workspace.set_workspace_area", "1.0.0")
async def set_workspace_area(ctx: RunContext, area: FocusArea | None) -> dict:
    """Set the place this workspace works in; null clears it.

    Either a lat/lon box, or places named with their country's code:
    `{"level": "adm1", "units": [names from list_areas], "iso": code from find_country}`,
    or a whole country as `{"level": "adm0", "units": [its name], "iso": its code}`.
    Later benchmarks and blends here are scored in this area unless they name their
    own; runs already made keep the area they used. Call this when the user names the
    place they work in, then tell them in a sentence what changed."""
    try:
        patch = workspace_settings.parse_patch(
            {"study_area": area.model_dump(mode="json") if area else None}
        )
    except workspace_settings.InvalidWorkspaceSettings as exc:
        raise Refusal(str(exc)) from exc
    return await _updated_settings(ctx, patch)
