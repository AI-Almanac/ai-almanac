"""Workspaces router.

POST /workspaces                                   create
GET  /workspaces                                   list mine
GET  /workspaces/{id}                              detail: settings, jobs + artifacts
GET  /workspaces/{id}/summary                      season state, setup, latest forecast, headline skill
GET  /workspaces/{id}/settings                     the workspace's defaults, e.g. its season
GET  /workspaces/{id}/settings/effective           the same, unset ones filled with platform defaults
PATCH /workspaces/{id}/settings                    change named settings; null clears one
PATCH /workspaces/{id}/artifacts/{aid}             rename a file; null restores its default name
PATCH /workspaces/{id}/analyses/{turn_id}          rename an analysis; null clears its name
POST /workspaces/{id}/jobs                         link a job
GET  /workspaces/{id}/addable-jobs                 the user's runs not in the workspace yet
GET  /workspaces/{id}/artifacts                    artifacts with breadcrumbs
GET  /workspaces/{id}/artifacts/{aid}/download     the file
GET  /workspaces/{id}/artifacts/{aid}/provenance   chain; ?verify=true re-hashes
POST /workspaces/promote                           workspace from an existing session
DELETE /workspaces/{id}/follow-ups/{fid}          cancel a queued follow-up
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from ai_almanac.server.auth import CurrentUser, require_workspaces

from ..services import (
    follow_ups,
    ledger,
    workspace_settings,
    workspace_summary,
    workspaces,
)
from ..services.run_summaries import OnsetDefinition
from ..services.workspace_settings import WorkspaceSettings
from ..services.workspace_summary import JobHeadline, WorkspaceSummary

router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], dependencies=[Depends(require_workspaces)]
)


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class WorkspaceOut(BaseModel):
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str


class Span(BaseModel):
    start: int
    end: int


class RunScope(BaseModel):
    """What tells a run from its neighbours; each part is null when its settings do not say."""

    place: str | None
    years: Span | None
    lead_days: Span | None


class WorkspaceJobOut(BaseModel):
    id: str
    job_type: str
    status: str
    dataset_id: str
    run_id: str | None
    model_name: str | None
    scope: RunScope
    created_at: str
    started_at: str | None
    completed_at: str | None
    error: str | None
    region_id: str | None
    blended: bool  # a forecast scored with a blend's weights, so it has an onset map
    # The areas the run was limited to, by name (or one lat/lon box in words); null
    # when it covered its whole region.
    area_of_interest: list[str] | None
    onset_definition: OnsetDefinition | None  # null for a job that uses none
    # For a job still to finish: how long a run like it has taken this user, start to
    # finish; null without such a past run.
    expected_seconds: int | None
    # What the result row leads with; a benchmark run's headline sits on its best model's job.
    headline: JobHeadline | None = None


class ArtifactOut(BaseModel):
    id: str
    label: str
    title: str
    group: str
    kind: str
    format: str
    created_at: str
    job_id: str | None
    step_id: str | None
    turn_id: str | None
    run_job_ids: list[str]  # nearest upstream jobs, oldest first; groups the file under their runs
    breadcrumb: list[str]
    summary: dict[str, Any]
    supporting: bool  # a file a result was made from, not a result itself


class AnalysisOut(BaseModel):
    """The name of the files one chat turn made."""

    turn_id: str
    title: str
    named_by: Literal["assistant", "user"]


class WorkspaceDetail(WorkspaceOut):
    settings: WorkspaceSettings
    jobs: list[WorkspaceJobOut]
    artifacts: list[ArtifactOut]
    analyses: list[AnalysisOut]


class RenameIn(BaseModel):
    title: str | None = Field(description="The new name; null goes back to the default")


class FollowUpOut(BaseModel):
    id: str
    session_id: str
    job_ids: list[str]
    instruction: str
    status: str
    created_at: str


class LinkJobIn(BaseModel):
    job_id: str
    # "added": an existing run picked by hand. "started": a run just started from the
    # workspace, which the record of how it was made already attributes.
    origin: Literal["added", "started"] = "added"


class PromoteIn(BaseModel):
    session_id: str


class ProvenanceStepOut(BaseModel):
    id: str
    created_at: str
    completed_at: str | None
    actor_kind: str
    primitive: str
    primitive_version: str
    description: str
    params: dict[str, Any]
    display_params: dict[str, Any]
    inputs: list[dict[str, Any]]
    outputs: list[str]
    runtime: dict[str, Any]
    job_id: str | None
    code_hash: str | None
    turn_id: str | None
    session_id: str | None
    ruleset_id: str | None
    ruleset_version: int | None
    model_name: str | None
    approval_id: str | None
    approved_at: str | None
    # When this step's run, made elsewhere, was added to the workspace, and whether a
    # person picked it or the assistant added it; both null when the run was made here.
    added_at: str | None = None
    added_by: Literal["user", "assistant"] | None = None
    parent_step_ids: list[str]
    step_hash: str
    verification: str | None


class ProvenanceArtifactOut(BaseModel):
    id: str
    label: str
    filename: str
    kind: str
    format: str
    content_hash: str
    provenance_step_id: str


class ProvenanceOut(BaseModel):
    artifact: ProvenanceArtifactOut
    steps: list[ProvenanceStepOut]
    artifacts: list[ProvenanceArtifactOut]
    producers: list[str]  # every step that output this artifact's bytes, oldest first
    lineage: str


def _out(row: dict) -> WorkspaceOut:
    return WorkspaceOut(**{key: row[key] for key in WorkspaceOut.model_fields})


async def _job_outs(workspace_id: str) -> list[WorkspaceJobOut]:
    """The workspace's jobs with the headline each result row leads with."""
    jobs, headlines = await asyncio.gather(
        workspaces.list_jobs(workspace_id), workspace_summary.job_headlines(workspace_id)
    )
    return [WorkspaceJobOut(**job, headline=headlines.get(job["id"])) for job in jobs]


async def owned_workspace(workspace_id: str, user: CurrentUser) -> dict:
    """The caller's own workspace; anyone else's is not found."""
    try:
        return await workspaces.require_workspace(workspace_id, user.id)
    except workspaces.WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


OwnedWorkspace = Annotated[dict, Depends(owned_workspace)]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(body: WorkspaceIn, user: CurrentUser):
    try:
        return _out(await workspaces.create_workspace(user.id, body.name, body.description))
    except workspaces.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(user: CurrentUser):
    return [_out(row) for row in await workspaces.list_workspaces(user.id)]


@router.post("/promote", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def promote_session(body: PromoteIn, user: CurrentUser):
    try:
        return _out(await workspaces.promote_session(body.session_id, user.id))
    except workspaces.WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{workspace_id}", response_model=WorkspaceDetail)
async def get_workspace(workspace: OwnedWorkspace):
    workspace_id = workspace["id"]
    return WorkspaceDetail(
        **_out(workspace).model_dump(),
        settings=workspace_settings.parse_settings(workspace["settings_json"]),
        jobs=await _job_outs(workspace_id),
        artifacts=[ArtifactOut(**item) for item in await workspaces.listing(workspace_id)],
        analyses=[AnalysisOut(**row) for row in await workspaces.list_analyses(workspace_id)],
    )


@router.get("/{workspace_id}/summary", response_model=WorkspaceSummary)
async def get_workspace_summary(workspace: OwnedWorkspace):
    """The facts above a workspace's results: season state, setup, latest forecast, skill."""
    workspace_id = workspace["id"]
    return await workspace_summary.summarize(workspace_id)


@router.patch("/{workspace_id}/artifacts/{artifact_id}", response_model=ArtifactOut)
async def rename_artifact(workspace: OwnedWorkspace, artifact_id: str, body: RenameIn):
    workspace_id = workspace["id"]
    try:
        renamed = await workspaces.rename_artifact(workspace_id, artifact_id, body.title)
    except workspaces.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not renamed:
        raise HTTPException(status_code=404, detail="File not found in this workspace")
    [item] = await workspaces.listing(workspace_id, only={artifact_id})
    return ArtifactOut(**item)


@router.patch("/{workspace_id}/analyses/{turn_id}", response_model=AnalysisOut | None)
async def rename_analysis(workspace: OwnedWorkspace, turn_id: str, body: RenameIn):
    """Name an analysis; null clears the name, so it shows the question again."""
    workspace_id = workspace["id"]
    try:
        named = await workspaces.name_analysis(workspace_id, turn_id, body.title, "user")
    except workspaces.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisOut(**named) if named else None


@router.get("/{workspace_id}/settings", response_model=WorkspaceSettings)
async def get_settings(workspace: OwnedWorkspace):
    workspace_id = workspace["id"]
    return await workspace_settings.get_settings(workspace_id)


@router.get("/{workspace_id}/settings/effective", response_model=WorkspaceSettings)
async def get_effective_settings(workspace: OwnedWorkspace):
    """What a run here uses today: the settings with platform defaults for unset ones."""
    workspace_id = workspace["id"]
    return workspace_settings.effective(await workspace_settings.get_settings(workspace_id))


# The body is taken raw so a bad write is answered in plain words, not FastAPI's
# error list; the schema is still published for clients.
_SETTINGS_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/WorkspaceSettings"}}
        },
    }
}


@router.patch(
    "/{workspace_id}/settings", response_model=WorkspaceSettings, openapi_extra=_SETTINGS_BODY
)
async def update_settings(
    workspace: OwnedWorkspace, body: Annotated[dict[str, Any], Body()]
) -> WorkspaceSettings:
    workspace_id = workspace["id"]
    try:
        patch = workspace_settings.parse_patch(body)
    except workspace_settings.InvalidWorkspaceSettings as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await workspace_settings.update_settings(workspace_id, patch)


EVENTS_POLL_SECONDS = 3.0
# Closes before Cloud Run's default 300 s request timeout; the client reconnects.
EVENTS_LIFETIME_SECONDS = 240.0


def _sse(event_type: str, **payload: object) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


async def _jobs_event(workspace_id: str) -> str:
    jobs = await _job_outs(workspace_id)
    return _sse("jobs", jobs=[job.model_dump() for job in jobs])


async def _artifacts_event(workspace_id: str, ids: set[str]) -> str:
    items = await workspaces.listing(workspace_id, only=ids)
    return _sse("artifacts", artifacts=[ArtifactOut(**item).model_dump() for item in items])


async def _analyses_event(workspace_id: str) -> str:
    rows = await workspaces.list_analyses(workspace_id)
    return _sse("analyses", analyses=[AnalysisOut(**row).model_dump() for row in rows])


async def _follow_ups_event(workspace_id: str) -> str:
    items = await follow_ups.list_for_workspace(workspace_id)
    return _sse("follow_ups", follow_ups=[FollowUpOut(**item).model_dump() for item in items])


async def _activity_events(
    workspace_id: str, since: str | None, request: Request
) -> AsyncIterator[str]:
    """Job lists, follow-ups and new artifacts, sent only when the cheap snapshot changes.

    ponytail: polls the database per open stream, since job status is written
    by the supervisor/Modal outside this process; a LISTEN/NOTIFY or pub/sub
    feed can replace the loop if many pages watch at once.
    """
    previous = await workspaces.activity(workspace_id)
    yield await _jobs_event(workspace_id)
    yield await _follow_ups_event(workspace_id)
    if unseen := workspaces.artifacts_since(previous, since):
        yield await _artifacts_event(workspace_id, unseen)
    deadline = time.monotonic() + EVENTS_LIFETIME_SECONDS
    while time.monotonic() < deadline and not await request.is_disconnected():
        await asyncio.sleep(EVENTS_POLL_SECONDS)
        current = await workspaces.activity(workspace_id)
        changes = workspaces.activity_changes(previous, current)
        if changes.jobs:
            yield await _jobs_event(workspace_id)
        if changes.new_artifacts:
            yield await _artifacts_event(workspace_id, changes.new_artifacts)
        if changes.follow_ups:
            yield await _follow_ups_event(workspace_id)
        if changes.analyses:
            yield await _analyses_event(workspace_id)
        if not changes.any:
            yield ": keep-alive\n\n"
        previous = current


@router.get("/{workspace_id}/events")
async def workspace_events(
    workspace: OwnedWorkspace,
    request: Request,
    since: str | None = Query(default=None, description="Newest artifact created_at held"),
):
    workspace_id = workspace["id"]
    return StreamingResponse(
        _activity_events(workspace_id, since, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{workspace_id}/jobs", response_model=list[WorkspaceJobOut])
async def link_job(workspace: OwnedWorkspace, body: LinkJobIn):
    workspace_id = workspace["id"]
    added_by = workspaces.ADDED_BY_USER if body.origin == "added" else None
    try:
        await workspaces.link_job(workspace_id, body.job_id, added_by=added_by)
    except workspaces.UnreadableJob as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _job_outs(workspace_id)


@router.get("/{workspace_id}/addable-jobs", response_model=list[WorkspaceJobOut])
async def list_addable_jobs(workspace: OwnedWorkspace, user: CurrentUser):
    """The user's own benchmarks, blends and forecasts that are not in the workspace."""
    workspace_id = workspace["id"]
    jobs = await workspaces.list_addable_jobs(workspace_id, user.id)
    return [WorkspaceJobOut(**job) for job in jobs]


@router.get("/{workspace_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(workspace: OwnedWorkspace):
    workspace_id = workspace["id"]
    return [ArtifactOut(**item) for item in await workspaces.listing(workspace_id)]


async def _artifact(workspace: dict, artifact_id: str) -> dict:
    artifact = await workspaces.resolve(workspace["id"], artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get("/{workspace_id}/artifacts/{artifact_id}/download")
async def download_artifact(workspace: OwnedWorkspace, artifact_id: str):
    """The file as stored, with its provenance chain stamped into this copy."""
    artifact = await _artifact(workspace, artifact_id)
    steps, _ = await ledger.chain(artifact)
    scratch = Path(tempfile.mkdtemp(prefix="download-"))
    copy = await asyncio.to_thread(ledger.stamped_copy, artifact, ledger.history(steps), scratch)
    if copy is None:
        shutil.rmtree(scratch, ignore_errors=True)
        raise HTTPException(status_code=404, detail="Artifact file is missing from storage")
    return FileResponse(
        copy,
        filename=f"{artifact['label']}{copy.suffix}",
        background=BackgroundTask(shutil.rmtree, scratch, ignore_errors=True),
    )


@router.get("/{workspace_id}/artifacts/{artifact_id}/provenance", response_model=ProvenanceOut)
async def artifact_provenance(
    workspace: OwnedWorkspace,
    artifact_id: str,
    verify: bool = Query(default=False),
):
    artifact = await _artifact(workspace, artifact_id)
    described = await workspaces.describe(workspace["id"], artifact, verify=verify)
    return ProvenanceOut(
        artifact=ProvenanceArtifactOut(**described["artifact"]),
        steps=[ProvenanceStepOut(**step) for step in described["steps"]],
        artifacts=[ProvenanceArtifactOut(**row) for row in described["artifacts"]],
        producers=described["producers"],
        lineage=described["lineage"],
    )


@router.delete("/{workspace_id}/follow-ups/{follow_up_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_follow_up(workspace: OwnedWorkspace, follow_up_id: str, user: CurrentUser) -> None:
    workspace_id = workspace["id"]
    if not await follow_ups.cancel(workspace_id, follow_up_id, user.id):
        raise HTTPException(status_code=404, detail="No queued follow-up to cancel")
