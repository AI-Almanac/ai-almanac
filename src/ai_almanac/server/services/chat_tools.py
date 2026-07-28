"""LLM/chat-facing wrappers around benchmark domain operations."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel
from pydantic import Field as PydanticField

from . import benchmark_domain, blend_domain
from .benchmark_state import BenchmarkRunSpec
from .blend_state import BlendRunSpec
from .chat_artifacts import create_chat_figure_artifact
from .chat_state import ChatScope


class PerModelRompParams(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    start_year_clim: int | None = None
    end_year_clim: int | None = None
    init_days: str | None = None
    date_filter_year: int | None = None
    parallel: bool | None = None
    probabilistic: bool | None = None
    members: str | None = None
    model_var: str | None = None
    file_pattern: str | None = None


class BenchmarkAdvancedParams(BaseModel):
    obs: str | None = None
    obs_file_pattern: str | None = None
    obs_var: str | None = None
    wet_threshold: float | None = None
    wet_init: float | None = None
    wet_spell: int | None = None
    dry_spell: int | None = None
    dry_extent: int | None = None
    nc_mask: str | None = None
    thresh_file: str | None = None
    ref_model: str | None = None
    ref_model_dir: str | None = None
    per_model_params: dict[str, PerModelRompParams] | None = None


class BenchmarkConfigPatch(BaseModel):
    intent: str | None = None
    region_id: str | None = None
    dataset_id: str | None = None
    model_ids: list[str] | None = None
    event_type: str | None = None
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


async def get_benchmark_config(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await benchmark_domain.get_benchmark_config(user_id, scope, session_id),
        session_id,
        user_id,
    )


async def update_benchmark_config(
    patch: dict, user_id: str, scope: ChatScope, session_id: str
) -> dict:
    return await tool_payload(
        await benchmark_domain.update_benchmark_config(patch, user_id, scope, session_id),
        session_id,
        user_id,
    )


async def validate_benchmark_config(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await benchmark_domain.validate_benchmark_config(user_id, scope, session_id),
        session_id,
        user_id,
    )


async def propose_benchmark_submit(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await benchmark_domain.propose_benchmark_submit(user_id, scope, session_id),
        session_id,
        user_id,
    )


async def submit_benchmark_for_session(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await benchmark_domain.submit_benchmark_for_session(user_id, scope, session_id),
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


async def rerun_job(request: RerunJobRequest, user_id: str, scope: ChatScope) -> dict:
    return await tool_payload(
        await benchmark_domain.rerun_job(request, user_id, scope), "", user_id
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


async def submit_blend_for_session(user_id: str, scope: ChatScope, session_id: str) -> dict:
    return await tool_payload(
        await blend_domain.submit_blend_for_session(user_id, scope, session_id),
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
