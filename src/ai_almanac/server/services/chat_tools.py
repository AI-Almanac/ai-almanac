"""LLM/chat-facing wrappers around benchmark domain operations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

from ai_almanac.server.services.focus_area import FocusArea, parse_country_code
from ai_almanac.server.services.romp import (
    DataPath,
    FilePattern,
    InitDays,
    Members,
    RompName,
    Year,
)

from . import benchmark_domain, blend_domain, boundaries, ledger, onset_rules, region_catalog
from .benchmark_state import BenchmarkEventType, BenchmarkRunSpec
from .blend_state import BlendRunSpec
from .chat_artifacts import create_chat_figure_artifact
from .chat_state import ChatScope


class PerModelRompParams(BaseModel):
    model_config = ConfigDict(title="PerModelRunParams")

    start_date: date | None = None
    end_date: date | None = None
    start_year_clim: Year | None = None
    end_year_clim: Year | None = None
    init_days: InitDays | None = None
    date_filter_year: Year | None = None
    parallel: bool | None = None
    probabilistic: bool | None = None
    members: Members | None = None
    model_var: RompName | None = None
    file_pattern: FilePattern | None = None


class BenchmarkAdvancedParams(BaseModel):
    obs: RompName | None = None
    obs_file_pattern: FilePattern | None = None
    obs_var: RompName | None = None
    wet_threshold: float | None = None
    wet_init: float | None = None
    wet_spell: int | None = None
    dry_spell: int | None = None
    dry_extent: int | None = None
    nc_mask: DataPath | None = None
    thresh_file: DataPath | None = None
    ref_model: RompName | None = None
    ref_model_dir: DataPath | None = None
    focus_area: FocusArea | None = None
    onset_rule: RompName | None = PydanticField(
        default=None,
        description=(
            "The `internal_value` of an onset definition from list_onset_rules; leave unset "
            "for the default definition. A value for tool calls, never a word for the user."
        ),
    )
    onset_rule_params: dict[RompName, Any] | None = PydanticField(
        default=None,
        description="Overrides for the onset rule's parameters; omitted ones keep their defaults.",
    )
    per_model_params: dict[str, PerModelRompParams] | None = None


class BenchmarkConfigPatch(BaseModel):
    intent: str | None = None
    region_id: str | None = None
    dataset_id: str | None = None
    model_ids: list[str] | None = None
    event_type: BenchmarkEventType | None = None
    forecast_window_days: Annotated[int, PydanticField(ge=30)] | None = None
    advanced_params: BenchmarkAdvancedParams | None = PydanticField(default=None)


SpatialMetricRequest = benchmark_domain.SpatialMetricRequest
CodeSandboxRequest = benchmark_domain.CodeSandboxRequest
JobCodeRequest = benchmark_domain.JobCodeRequest
RerunJobRequest = benchmark_domain.RerunJobRequest


class SubmitBenchmarkApproval(BaseModel):
    tool_call_id: str
    approved_config: BenchmarkRunSpec | None = None


class BlendConfigPatch(BaseModel):
    intent: str | None = None
    name: str | None = None
    obs_dataset_id: str | None = None
    model_ids: list[str] | None = None
    training_years: str | None = None
    cv_holdout_years: str | None = None
    forecast_years: str | None = None
    true_holdout_years: str | None = None
    formula_text: str | None = None
    focus_area: FocusArea | None = None
    threshold_mm: float | None = PydanticField(
        default=None,
        description="Onset rainfall threshold in mm over the onset window. Optional; "
        "workflow default is 20 mm.",
    )
    cutoff_month_day: str | None = PydanticField(
        default=None,
        description="MM-DD from which onset is searched each season (also the first "
        "forecast issue date). Optional; workflow default is 05-01.",
    )
    ref_onset_month_day: str | None = PydanticField(
        default=None,
        description="MM-DD reference (climatological) onset date the onset-before-"
        "reference probability is scored against. Optional; workflow default is 06-01.",
    )


class SubmitBlendApproval(BaseModel):
    tool_call_id: str
    approved_config: BlendRunSpec | None = None


async def tool_payload(raw_result: object, session_id: str, user_id: str) -> dict:
    if isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError:
            parsed = {"raw": raw_result}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    if not isinstance(raw_result, dict):
        return {"value": raw_result}

    parsed = dict(raw_result)
    sanitized_artifacts = []
    for artifact_meta in raw_result.get("artifacts", []):
        if not isinstance(artifact_meta, dict):
            continue
        data = artifact_meta.get("data")
        if artifact_meta.get("kind") == "figure" and isinstance(data, (bytes, bytearray)):
            artifact = await create_chat_figure_artifact(
                session_id,
                user_id,
                bytes(data),
                label=artifact_meta.get("label"),
                filename=artifact_meta.get("filename"),
                media_type=artifact_meta.get("media_type"),
            )
            sanitized_artifacts.append(
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "url": artifact.url,
                    "label": artifact.label,
                    "media_type": artifact.media_type,
                    "filename": artifact.filename,
                    "created_at": artifact.created_at.isoformat(),
                }
            )

    payload = {key: value for key, value in parsed.items() if key != "artifacts"}
    if sanitized_artifacts:
        payload["artifacts"] = sanitized_artifacts
    return payload


def _named_list_payload(payload: dict, key: str) -> dict:
    value = payload.get("value")
    return {key: value} if isinstance(value, list) else payload


async def list_regions(user_id: str, scope: ChatScope) -> dict:
    payload = await tool_payload(await benchmark_domain.list_regions(user_id, scope), "", user_id)
    return _named_list_payload(payload, "regions")


class AreasUnavailable(ValueError):
    """A region's areas at a level cannot be named, with the reason in the user's terms."""


@dataclass(frozen=True)
class RegionAreas:
    region: dict
    level: boundaries.AreaLevel
    units: dict
    source: dict
    areas: list[dict]


async def _country_place(country: str) -> dict:
    """A country as the place whose areas are named: no catalogue region behind it."""
    try:
        iso = parse_country_code(country)
    except ValueError as exc:
        raise AreasUnavailable(f"{exc}. Call find_country.") from exc
    try:
        known = boundaries.match_countries(await boundaries.list_countries(), iso)
    except boundaries.BoundaryUnavailable as exc:
        raise AreasUnavailable(f"The list of countries is unavailable: {exc}") from exc
    if not known:
        raise AreasUnavailable(f"No country has the code {iso}. Call find_country.")
    return {"id": None, "display_name": known[0]["name"], "boundary_iso": iso}


async def _region_place(region_id: str) -> dict:
    region = await region_catalog.get_region(region_id.strip())
    if region is None:
        raise AreasUnavailable(f"No region {region_id!r}. Call list_regions.")
    if not region.get("boundary_iso"):
        raise AreasUnavailable(
            f"{region['display_name']} has no administrative boundaries, so its "
            "areas have no names here. Use a lat/lon box instead."
        )
    return region


async def load_region_areas(
    region_id: str | None,
    level: boundaries.AreaLevel,
    within: str | None = None,
    country: str | None = None,
) -> RegionAreas:
    """The units at ``level`` of a country, or of a catalogue region when no country
    is named, each with the parent unit holding it, and only those inside ``within``
    when it is given."""
    if country:
        region = await _country_place(country)
    elif region_id:
        region = await _region_place(region_id)
    else:
        raise AreasUnavailable(
            "Name a region from list_regions, or a country by its three-letter code."
        )
    parent_level = boundaries.PARENT_LEVEL.get(level)
    if within and parent_level is None:
        raise AreasUnavailable(f"{level} areas sit inside no larger area; leave out within.")
    try:
        units = await boundaries.load_boundary(region["boundary_iso"], level)
        parents = (
            await boundaries.load_boundary(region["boundary_iso"], parent_level)
            if parent_level
            else None
        )
    except boundaries.BoundaryUnavailable as exc:
        raise AreasUnavailable(
            f"The {level} boundaries for {region['display_name']} are unavailable: {exc}"
        ) from exc
    areas = await asyncio.to_thread(
        boundaries.name_areas, units["geojson"], parents["geojson"] if parents else None
    )
    if within:
        areas = boundaries.areas_within(areas, within)
    return RegionAreas(region, level, units["geojson"], units.get("metadata") or {}, areas)


async def list_areas(
    region_id: str | None,
    level: boundaries.AreaLevel,
    within: str | None,
    country: str | None = None,
) -> dict:
    try:
        found = await load_region_areas(region_id, level, within, country)
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "region": found.region["id"],
        "country": found.region["boundary_iso"],
        "level": level,
        "count": len(found.areas),
        "areas": found.areas,
    }


async def find_country(name: str) -> dict:
    """The country a name or three-letter code points at, with the code places are
    picked by."""
    try:
        matches = boundaries.match_countries(await boundaries.list_countries(), name)
    except boundaries.BoundaryUnavailable as exc:
        return {"error": f"The list of countries is unavailable: {exc}"}
    if not matches:
        return {"error": f"No country matches {name!r}."}
    return {"countries": matches[:8], "count": len(matches)}


async def list_datasets(region: str | None, user_id: str, scope: ChatScope) -> dict:
    payload = await tool_payload(
        await benchmark_domain.list_datasets(region, user_id, scope), "", user_id
    )
    return _named_list_payload(payload, "datasets")


async def list_models(region: str | None, user_id: str, scope: ChatScope) -> dict:
    payload = await tool_payload(
        await benchmark_domain.list_models(region, user_id, scope), "", user_id
    )
    return _named_list_payload(payload, "models")


def list_onset_rules() -> dict:
    """The onset definitions a benchmark can score against. ``display_name`` and
    ``description`` are what to say to the user, each parameter's ``label`` likewise;
    ``internal_value`` is only the value to set as advanced_params.onset_rule. The
    default is named by its display name alone: leaving onset_rule unset selects it."""
    return {
        "onset_definitions": onset_rules.assistant_listing(),
        "default_display_name": onset_rules.display_name(None),
        "internal_values": (
            "Each definition's `internal_value` and its parameters' `name` are values for "
            "tool calls only. Never write them in a reply or in notes; say the display "
            "name and the parameter labels."
        ),
    }


def with_onset_definition_names(payload: Any) -> Any:
    """A benchmark plan with each onset rule it mentions named for the user: wherever
    settings carry ``onset_rule``, ``onset_definition`` beside it holds the display
    name, so a reply need not repeat the identifier. An unset rule is the default."""
    if isinstance(payload, list):
        return [with_onset_definition_names(item) for item in payload]
    if not isinstance(payload, dict):
        return payload
    named = {key: with_onset_definition_names(value) for key, value in payload.items()}
    rule = payload.get("onset_rule")
    if "onset_rule" in payload and (rule is None or isinstance(rule, str)):
        named["onset_definition"] = onset_rules.display_name(rule)
    return named


async def get_benchmark_config(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return with_onset_definition_names(
        await tool_payload(
            await benchmark_domain.get_benchmark_config(user_id, scope, session_id),
            session_id,
            user_id,
        )
    )


async def update_benchmark_config(
    patch: dict, user_id: str, scope: ChatScope, session_id: str
) -> dict:
    return with_onset_definition_names(
        await tool_payload(
            await benchmark_domain.update_benchmark_config(patch, user_id, scope, session_id),
            session_id,
            user_id,
        )
    )


async def validate_benchmark_config(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return with_onset_definition_names(
        await tool_payload(
            await benchmark_domain.validate_benchmark_config(user_id, scope, session_id),
            session_id,
            user_id,
        )
    )


async def propose_benchmark_submit(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return with_onset_definition_names(
        await tool_payload(
            await benchmark_domain.propose_benchmark_submit(user_id, scope, session_id),
            session_id,
            user_id,
        )
    )


async def submit_benchmark_for_session(
    user_id: str, scope: ChatScope, session_id: str, attribution: ledger.Attribution
) -> dict:
    return await tool_payload(
        await benchmark_domain.submit_benchmark_for_session(
            user_id, scope, session_id, attribution
        ),
        session_id,
        user_id,
    )


async def list_jobs(user_id: str, scope: ChatScope, status: str | None = None) -> dict:
    payload = await tool_payload(
        await benchmark_domain.list_jobs(user_id, scope, status), "", user_id
    )
    return _named_list_payload(payload, "jobs")


async def get_job_info(job_id: str, user_id: str, scope: ChatScope) -> dict:
    return await tool_payload(
        await benchmark_domain.get_job_info(job_id, user_id, scope), "", user_id
    )


async def get_job_logs(job_id: str, max_chars: int, user_id: str, scope: ChatScope) -> dict:
    return await tool_payload(
        await benchmark_domain.get_job_logs(job_id, max_chars, user_id, scope),
        "",
        user_id,
    )


async def rerun_job(
    request: RerunJobRequest, user_id: str, scope: ChatScope, attribution: ledger.Attribution
) -> dict:
    return await tool_payload(
        await benchmark_domain.rerun_job(request, user_id, scope, attribution), "", user_id
    )


async def get_job_metrics(job_id: str, user_id: str, scope: ChatScope) -> dict:
    return await tool_payload(
        await benchmark_domain.get_job_metrics(job_id, user_id, scope), "", user_id
    )


async def get_skill_scores(job_id: str, user_id: str, scope: ChatScope) -> dict:
    return await tool_payload(
        await benchmark_domain.get_skill_scores(job_id, user_id, scope), "", user_id
    )


async def get_spatial_summary(
    request: SpatialMetricRequest, user_id: str, scope: ChatScope
) -> dict:
    return await tool_payload(
        await benchmark_domain.get_spatial_summary(request, user_id, scope),
        "",
        user_id,
    )


async def run_code_sandbox(
    request: CodeSandboxRequest, user_id: str, scope: ChatScope, session_id: str
) -> dict:
    return await tool_payload(
        await benchmark_domain.run_code_sandbox(request, user_id, scope),
        session_id,
        user_id,
    )


async def run_code(
    request: JobCodeRequest, user_id: str, scope: ChatScope, session_id: str
) -> dict:
    return await tool_payload(
        await benchmark_domain.run_code(request, user_id, scope), session_id, user_id
    )


async def get_current_benchmark_config(session_id: str, user_id: str) -> BenchmarkRunSpec:
    return await benchmark_domain.get_current_benchmark_config(session_id, user_id)


benchmark_payload = benchmark_domain.benchmark_payload
validation_for_config = benchmark_domain.validation_for_config
is_tool_available = benchmark_domain.is_tool_available


# --- Blend setup wrappers -------------------------------------------------


async def list_blend_models(region: str | None, user_id: str, scope: ChatScope) -> dict:
    payload = await tool_payload(
        await blend_domain.list_blend_models(region, user_id, scope), "", user_id
    )
    return _named_list_payload(payload, "models")


async def get_blend_config(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await blend_domain.get_blend_config(user_id, scope, session_id),
        session_id,
        user_id,
    )


async def update_blend_config(patch: dict, user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await blend_domain.update_blend_config(patch, user_id, scope, session_id),
        session_id,
        user_id,
    )


async def validate_blend_config(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await blend_domain.validate_blend_config(user_id, scope, session_id),
        session_id,
        user_id,
    )


async def propose_blend_submit(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await blend_domain.propose_blend_submit(user_id, scope, session_id),
        session_id,
        user_id,
    )


async def submit_blend_for_session(
    user_id: str, scope: ChatScope, session_id: str, attribution: ledger.Attribution
) -> dict:
    return await tool_payload(
        await blend_domain.submit_blend_for_session(user_id, scope, session_id, attribution),
        session_id,
        user_id,
    )


async def get_current_blend_config(session_id: str, user_id: str) -> BlendRunSpec:
    return await blend_domain.get_current_blend_config(session_id, user_id)


async def get_blend_results(job_id: str, user_id: str, scope: ChatScope) -> dict:
    return await tool_payload(
        await blend_domain.get_blend_results(job_id, user_id, scope), "", user_id
    )


blend_payload = blend_domain.blend_payload
blend_validation_for_config = blend_domain.validation_for_config
