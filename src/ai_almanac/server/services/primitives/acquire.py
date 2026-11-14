"""Bringing data into a workspace: fetching from public sources, and opening part of a
registered model archive or observation dataset."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from pathlib import Path

from ai_almanac_tools import sources as tool_sources
from ai_almanac_tools.sources import FetchRequest
from pydantic_ai import RunContext

from ai_almanac.settings import settings

from .. import (
    approval_display,
    boundaries,
    code_sandbox,
    data_sources,
    ledger,
    region_catalog,
    source_subset,
    tools_sandbox,
    workspaces,
)
from ..approval_display import Fact
from ..focus_area import FocusArea, FocusUnits, lat_lon_label
from ..source_subset import megabytes, subset_area_label
from ..workspace_display import day_label
from .code_harness import harness_outcome
from .registry import Refusal, output_dir, primitive

logger = logging.getLogger(__name__)


def fetch_available() -> bool:
    """Fetches run in a sandbox that can be let through to a source's hosts: srt only.

    ponytail: on Modal deployments fetch stays hidden until its runner exists (D13).
    """
    runner = code_sandbox.configured_runner()
    return runner is not None and runner.reaches_network and runner.ready()


@primitive("acquire.resolve_region", "1.0.0", available=fetch_available)
async def resolve_region(ctx: RunContext, name: str) -> dict:
    """Resolve a region name or id to its bounding box from the region catalog. Use it
    for a fetch's box rather than guessing coordinates."""
    needle = name.strip().lower()
    regions = await region_catalog.list_regions()
    for region in regions:
        if needle in {
            str(region.get("id", "")).lower(),
            str(region.get("display_name", "")).lower(),
        }:
            return {
                "id": region["id"],
                "display_name": region["display_name"],
                "bbox": [
                    region["lat_min"],
                    region["lon_min"],
                    region["lat_max"],
                    region["lon_max"],
                ],
                "romp_name": region.get("romp_name"),
            }
    raise Refusal(f"No region matches {name!r}", known_regions=[region["id"] for region in regions])


@primitive("acquire.list_sources", "1.0.0", available=fetch_available)
async def list_sources(ctx: RunContext) -> dict:
    """List the public data sources fetch can read, with each one's variables, units and grid
    spacing, and the last day each has data for. Ask only for days up to then."""
    return {
        "sources": [
            {
                "name": source.name,
                "description": source.description,
                "grid_deg": source.grid_deg,
                "record_end": _iso(await _record_end(source)),
                "variables": {
                    name: {"description": var.description, "units": var.units}
                    for name, var in source.variables.items()
                },
            }
            for source in tool_sources.SOURCES.values()
        ]
    }


_RECORD_END_TTL_SECONDS = 6 * 3600
_record_ends: dict[str, tuple[float, date]] = {}


async def _record_end(source: tool_sources.Source) -> date | None:
    """The source's last day of data, looked up at most every few hours.

    None when the source has no end or cannot be reached; the fetch itself still
    refuses days past the record, only after approval instead of before.
    """
    if source.record_end is None:
        return None
    cached = _record_ends.get(source.name)
    if cached and time.monotonic() - cached[0] < _RECORD_END_TTL_SECONDS:
        return cached[1]
    try:
        end = await asyncio.to_thread(source.record_end)
    except Exception:
        logger.warning("Could not look up the record end of %s", source.name, exc_info=True)
        return None
    _record_ends[source.name] = (time.monotonic(), end)
    return end


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


async def _fetch_plan(request: FetchRequest) -> tool_sources.FetchPlan:
    """The request checked against the source catalog, its record and the size limit."""
    try:
        plan = tool_sources.plan_fetch(request)
    except (KeyError, ValueError) as exc:
        raise Refusal(str(exc).strip("'\""), ok=False) from exc
    end = await _record_end(tool_sources.get_source(request.source))
    if end and request.end > end:
        raise Refusal(
            f"{request.source} has data only up to {end}; ask for days up to then.",
            ok=False,
            record_end=end.isoformat(),
        )
    if plan.estimated_bytes > settings.max_fetch_bytes:
        raise Refusal(
            f"This fetch is about {megabytes(plan.estimated_bytes)}, over the "
            f"{megabytes(settings.max_fetch_bytes)} limit. Narrow the box, dates or variables.",
            ok=False,
        )
    return plan


@primitive("acquire.plan_fetch", "1.0.0", available=fetch_available)
async def plan_fetch(ctx: RunContext, request: FetchRequest) -> dict:
    """Check a fetch request against the source catalog and estimate its size, without
    fetching anything. Call it before fetch and tell the user the estimate."""
    plan = await _fetch_plan(request)
    return {
        "ok": True,
        "request": request.model_dump(mode="json"),
        "days": request.days,
        "grid_points": plan.grid_points,
        "estimated_size": megabytes(plan.estimated_bytes),
    }


async def _fetch_refusal(request: FetchRequest) -> None:
    await _fetch_plan(request)


async def _describe_fetch(ctx: RunContext, request: FetchRequest) -> list[Fact]:
    return _fetch_facts(request, tool_sources.get_source(request.source), _estimated_size(request))


def _estimated_size(request: FetchRequest) -> str | None:
    try:
        return megabytes(tool_sources.plan_fetch(request).estimated_bytes)
    except (KeyError, ValueError):
        return None


def _fetch_facts(
    request: FetchRequest, source: tool_sources.Source, size: str | None
) -> list[Fact]:
    variables = ", ".join(
        f"{var.description} ({var.units})" if (var := source.variables.get(name)) else name
        for name in request.variables
    )
    return approval_display.facts(
        ("Source", source.description),
        ("Variables", variables),
        ("Area", lat_lon_label(request.lat_bounds, request.lon_bounds)),
        ("Dates", approval_display.date_range_label(request.start, request.end)),
        ("Estimated size", size),
    )


@primitive(
    "acquire.fetch",
    "1.0.0",
    needs_approval=True,
    available=fetch_available,
    precheck=_fetch_refusal,
    describe=_describe_fetch,
)
async def fetch(ctx: RunContext, request: FetchRequest) -> dict:
    """Copy a subset of a public source into the workspace as a dataset artifact. Needs the
    user's approval; call plan_fetch first. Days past the end of the source's record are
    refused with the last available day."""
    source = tool_sources.get_source(request.source)
    with output_dir("fetch") as out_dir:
        ran = await tools_sandbox.run_fetch(
            request.model_dump_json(),
            hosts=source.hosts,
            into=out_dir,
            timeout=settings.fetch_timeout_seconds,
        )
        outcome = harness_outcome(ran.run)
        if not outcome.get("ok"):
            raise Refusal(outcome.get("error", "The fetch failed"), ok=False)
        taken = {path.name: path for path in ran.outputs}
        fetched = taken.get(Path(str(outcome.get("file", ""))).name)
        if fetched is None:
            raise Refusal("The fetch produced no dataset", ok=False)
    label = workspaces.safe_label(
        f"{source.name}_{'_'.join(request.variables)}_{request.start}_{request.end}"
    )
    return {
        "ok": True,
        "source_version": outcome["source_version"],
        "_outputs": [ledger.OutputFile(path=fetched, label=label, kind="dataset")],
        "_runtime": {
            **ran.runtime,
            "source": source.name,
            "source_catalog_version": source.version,
            "source_version": outcome["source_version"],
        },
    }


async def _readable_source(source_id: str, user_id: str) -> dict:
    source = await data_sources.get_source(source_id)
    if source is None or not data_sources.visible_to(source, user_id):
        raise Refusal(f"No data source {source_id!r}. Call list_datasets or list_models.", ok=False)
    if source.get("status") != "ready":
        raise Refusal(f"{source['name']} is not ready to read.", ok=False)
    try:
        data_sources.readable_location(source)
    except data_sources.UnreadableFiles as exc:
        raise Refusal(f"{exc}.", ok=False) from exc
    return source


async def _subset(
    source: dict, start: date, end: date, area: FocusArea | None
) -> source_subset.Subset:
    try:
        if isinstance(area, FocusUnits):
            region = await region_catalog.get_region(source.get("region") or "")
            area = await boundaries.attach_unit_outlines(area, region)
        return source_subset.Subset.parse(start, end, area)
    except (ValueError, boundaries.BoundaryUnavailable) as exc:
        raise Refusal(str(exc), ok=False) from exc


def _opened_title(source_name: str, area: FocusArea | None, start: date, end: date) -> str:
    where = f", {subset_area_label(area)}" if area is not None else ""
    return f"{source_name}{where}, {day_label(start)} to {day_label(end)}"


@primitive("acquire.open_source", "1.0.0")
async def open_source(
    ctx: RunContext, source_id: str, start: date, end: date, area: FocusArea | None = None
) -> dict:
    """Copy part of a registered model archive or observation dataset into the workspace as
    a dataset artifact. Use it to look at a model's past forecasts or the observations
    directly, without running a benchmark. `source_id` is a dataset id from list_datasets or
    a model id from list_models. A model archive is selected by forecast start date between
    `start` and `end` and keeps every lead day; observations by day. `area` limits it to a
    lat/lon box or to named adm1 or adm2 areas from list_areas; omit it for the whole
    source. A subset too large to work with is refused with its size: narrow the dates or
    the area."""
    source = await _readable_source(source_id, ctx.deps.user_id)
    subset = await _subset(source, start, end, area)
    with output_dir("open_source") as out_dir:
        out_path = out_dir / "subset.nc"
        try:
            staged = await asyncio.to_thread(source_subset.stage_subset, source, subset, out_path)
        except ValueError as exc:
            raise Refusal(str(exc), ok=False) from exc
    return {
        "ok": True,
        "source": source["name"],
        **staged,
        "_outputs": [
            ledger.OutputFile(
                path=out_path,
                label=workspaces.safe_label(f"{source['name']}_{start}_{end}"),
                kind="dataset",
                title=_opened_title(source["name"], area, start, end),
            )
        ],
        "_params": {
            "source_name": source["name"],
            "source_kind": source["kind"],
            "source_version": source.get("updated_at"),
            "area_name": subset_area_label(area),
        },
    }
