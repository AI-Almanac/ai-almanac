"""Interface actions: what the assistant can ask the workspace to show.

An action is an intent with a typed payload (open this panel, show this run), never a
click. A tool here only parses its arguments and returns the action; the browser sees
the tool result and carries it out. The server does not wait for the browser and does
not know whether it acted, so every result says what was offered, never what happened.
An action starts nothing: a run begins when the user presses the button or approves a
card. See docs/assistant-interface-actions-design.md.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from . import chat_tools, workspaces
from .chat_tools import ChatScope
from .focus_area import FocusArea

# The key a tool result carries its action under; the browser looks for it.
ACTION_KEY = "interface_action"


class BenchmarkDraft(BaseModel):
    """What to fill a new benchmark's setup form with. Leave out what the user has not settled."""

    panel: Literal["benchmark_setup"] = "benchmark_setup"
    region_id: str | None = Field(default=None, description="Region id from list_regions.")
    dataset_id: str | None = Field(
        default=None, description="Observation dataset id from list_datasets."
    )
    model_ids: list[str] = Field(default_factory=list, description="Model ids from list_models.")
    focus_area: FocusArea | None = Field(
        default=None, description="Area of interest, as for submit_benchmark."
    )


class BlendDraft(BaseModel):
    """What to fill a new blend's setup form with. Leave out what the user has not settled."""

    panel: Literal["blend_setup"] = "blend_setup"
    name: str = ""
    obs_dataset_id: str | None = Field(default=None, description="Observation data source id.")
    model_ids: list[str] = Field(
        default_factory=list, description="Model source ids from list_blend_models."
    )
    training_years: str = Field(default="", description='Years like "2000:2015".')
    cv_holdout_years: str = ""
    forecast_years: str = ""
    true_holdout_years: str = ""
    formula_text: str = ""
    focus_area: FocusArea | None = Field(
        default=None, description="Area of interest, as for submit_blend."
    )
    threshold_mm: float | None = None
    cutoff_month_day: str = Field(default="", description="MM-DD the onset search starts.")
    ref_onset_month_day: str = Field(default="", description="MM-DD reference onset date.")


class ForecastDraft(BaseModel):
    """What to fill a new forecast's setup form with. The form runs a completed blend's
    forecast; a raw forecast with no blend is not set up here, so use run_forecast for one."""

    panel: Literal["forecast_setup"] = "forecast_setup"
    blend_id: str | None = Field(
        default=None, description="A completed blend's job id, from find_runs."
    )
    models: list[str] = Field(
        default_factory=list,
        description="Names of the blend's models to run; empty leaves the choice to the user.",
    )


PanelDraft = Annotated[BenchmarkDraft | BlendDraft | ForecastDraft, Field(discriminator="panel")]

_PANEL_WORDS = {
    "benchmark_setup": "new benchmark",
    "blend_setup": "new blend",
    "forecast_setup": "new forecast",
}


class ShowRun(BaseModel):
    """A run in this workspace, by the job ids find_runs gave for it."""

    kind: Literal["run"] = "run"
    job_ids: list[str] = Field(min_length=1)


class ShowFile(BaseModel):
    """A file in this workspace, by its label or id from list_artifacts."""

    kind: Literal["file"] = "file"
    artifact: str


ShowTarget = Annotated[ShowRun | ShowFile, Field(discriminator="kind")]


def offered(action: dict, said: str) -> dict:
    """A tool result carrying an action for the browser, and what to tell the user."""
    return {ACTION_KEY: action, "offered": said}


def _ids(listing: dict, key: str) -> set[str] | None:
    """The ids in a catalog listing; None when it could not be read, so nothing is refused."""
    items = listing.get(key)
    if not isinstance(items, list):
        return None
    return {str(item["id"]) for item in items if isinstance(item, dict) and "id" in item}


def unknown_ids(wanted: list[str], known: set[str] | None) -> list[str]:
    return [] if known is None else [item for item in wanted if item not in known]


def _refusal(what: str, unknown: list[str], listing_tool: str) -> dict:
    return {"error": f"No {what} {', '.join(unknown)}. Call {listing_tool} for the ids."}


async def _benchmark_refusal(draft: BenchmarkDraft, user_id: str, scope: ChatScope) -> dict | None:
    if draft.dataset_id:
        datasets = _ids(await chat_tools.list_datasets(None, user_id, scope), "datasets")
        if unknown := unknown_ids([draft.dataset_id], datasets):
            return _refusal("observation dataset", unknown, "list_datasets")
    if draft.model_ids:
        models = _ids(await chat_tools.list_models(draft.region_id, user_id, scope), "models")
        if unknown := unknown_ids(draft.model_ids, models):
            return _refusal("model", unknown, "list_models")
    return None


async def _blend_refusal(draft: BlendDraft, user_id: str, scope: ChatScope) -> dict | None:
    if not draft.model_ids:
        return None
    models = _ids(await chat_tools.list_blend_models(None, user_id, scope), "models")
    if unknown := unknown_ids(draft.model_ids, models):
        return _refusal("model", unknown, "list_blend_models")
    return None


async def _forecast_refusal(draft: ForecastDraft, workspace_id: str) -> dict | None:
    if draft.blend_id and draft.blend_id not in await _workspace_job_ids(workspace_id):
        return {"error": "That blend is not in this workspace. Find it with find_runs and add it."}
    return None


async def _workspace_job_ids(workspace_id: str) -> set[str]:
    return {str(job["id"]) for job in await workspaces.list_jobs(workspace_id)}


async def panel_refusal(draft: PanelDraft, user_id: str, scope: ChatScope) -> dict | None:
    """Why this draft cannot be offered, as a tool error to act on; None when it can."""
    if isinstance(draft, BenchmarkDraft):
        return await _benchmark_refusal(draft, user_id, scope)
    if isinstance(draft, BlendDraft):
        return await _blend_refusal(draft, user_id, scope)
    return await _forecast_refusal(draft, scope.key)


def open_panel_action(draft: PanelDraft) -> dict:
    return offered(
        {
            "kind": "open_panel",
            "panel": draft.panel,
            "draft": draft.model_dump(mode="json", exclude={"panel"}, exclude_none=True),
        },
        f"The {_PANEL_WORDS[draft.panel]} form is offered to the user, filled in with this "
        "draft. Nothing has been started: they review it, change what they like and start "
        "it themselves. Now reply to the user in one or two sentences: what you filled in and what is left for them to choose. Do not say it is running.",
    )


async def show_action(target: ShowTarget, workspace_id: str) -> dict:
    if isinstance(target, ShowFile):
        artifact = await workspaces.resolve(workspace_id, target.artifact)
        if artifact is None:
            return {"error": f"No file {target.artifact!r} here. Call list_artifacts."}
        shown: dict = {"kind": "file", "artifact_id": artifact["id"]}
    else:
        if set(target.job_ids) - await _workspace_job_ids(workspace_id):
            return {"error": "That run is not in this workspace. Find it with find_runs."}
        shown = {"kind": "run", "job_ids": target.job_ids}
    return offered(
        {"kind": "show", "target": shown},
        "It is offered to the user on the stage beside the conversation. Carry on with your reply.",
    )


async def open_panel(ctx: RunContext, draft: PanelDraft) -> dict:
    """Open a setup form in the workspace, filled in, for the user to review and start
    themselves. Use it when the user asks you to set something up, wants to adjust
    settings before running, or has not clearly asked you to run it. When they have
    clearly asked you to run or train it, use submit_benchmark, submit_blend or
    run_forecast instead, which ask for their approval. This starts nothing."""
    if ctx.deps.workspace_id is None:
        return {"error": workspaces.NOT_IN_CONVERSATION}
    if refusal := await panel_refusal(draft, ctx.deps.user_id, ctx.deps.scope):
        return refusal
    return open_panel_action(draft)


async def show(ctx: RunContext, target: ShowTarget) -> dict:
    """Bring a run or a file of this workspace up on the stage beside the conversation,
    when the user asks to see it or your reply is about it."""
    if ctx.deps.workspace_id is None:
        return {"error": workspaces.NOT_IN_CONVERSATION}
    return await show_action(target, ctx.deps.scope.key)


def interface_toolset() -> FunctionToolset:
    """The interface actions a workspace conversation can take."""
    toolset = FunctionToolset(id="interface")
    toolset.tool(open_panel)
    toolset.tool(show)
    return toolset
