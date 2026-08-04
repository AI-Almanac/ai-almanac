"""Assistant ruleset administration.

Admin-only. Lets an operator read, clone, edit, preview and activate the
assistant's rulesets at runtime, which is the point of storing them as rows
rather than as Python constants.

Deliberately absent: any way for a *conversation* to reach these endpoints. The
assistant has no tool that names a ruleset, and ``test_chat_prompt.py`` asserts
that stays true. The rules the platform enforces live in ``services.guardrails``
and are not editable here at all — only the wording that explains them is.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai_almanac.server.auth import AdminUser
from ai_almanac.server.services import assistant_compare, guardrails, rulesets
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.llm import _instructions_for_ruleset
from ai_almanac.server.services.rulesets import PromptSection, Ruleset, ToolPolicy

from .chat import require_chat_available

router = APIRouter(prefix="/assistant", tags=["assistant"])

# Scope kinds a preview can be rendered for; mirrors ChatScope['kind'].
_PREVIEW_SCOPE_KINDS = ("benchmark_setup", "benchmark_run_group", "blend_setup", "job_set")


class RulesetSummary(BaseModel):
    id: str
    name: str
    description: str
    version: int
    source: str
    is_active: bool
    section_keys: list[str]
    denied_tools: list[str]
    model: str | None


class RulesetDetail(BaseModel):
    id: str
    name: str
    description: str
    version: int
    source: str
    is_active: bool
    prompt_sections: list[PromptSection]
    tool_policy: ToolPolicy
    model: str | None
    model_settings: dict | None


class GuardrailThresholds(BaseModel):
    """The enforced thresholds, read-only here.

    Surfaced so an admin editing prose can see the numbers the {{placeholders}}
    will resolve to. Editing them is a platform setting (PATCH /settings), not a
    ruleset edit, because the submission chokepoint reads the same value.
    """

    min_onset_years: int
    min_training_years: int
    blend_member_warn: int
    small_sample_years: int
    presatellite_end_year: int


class RulesetSave(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=140)
    description: str = Field(default="", max_length=2000)
    version: int = Field(default=1, ge=1)
    prompt_sections: list[PromptSection]
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    model: str | None = None
    model_settings: dict | None = None


class VariantIn(BaseModel):
    """One arm of a comparison: a ruleset, optionally on a different model."""

    ruleset_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=200)


class CompareRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    variants: list[VariantIn] = Field(min_length=2, max_length=2)
    # Clone an existing conversation to compare against its configuration state;
    # omit for a fresh pair of sessions.
    source_session_id: str | None = None
    scope: ChatScope | None = None


class VoteIn(BaseModel):
    """Which arm won. ``None`` records a tie."""

    winner_session_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class VoteOut(BaseModel):
    rated_turns: int


class PreviewRequest(BaseModel):
    scope_kind: str = "blend_setup"


class PreviewResult(BaseModel):
    scope_kind: str
    instructions: str
    character_count: int


def _summary(ruleset: Ruleset, is_active: bool) -> RulesetSummary:
    return RulesetSummary(
        id=ruleset.id,
        name=ruleset.name,
        description=ruleset.description,
        version=ruleset.version,
        source=ruleset.source,
        is_active=is_active,
        section_keys=[section.key for section in ruleset.prompt_sections],
        denied_tools=list(ruleset.tool_policy.deny),
        model=ruleset.model,
    )


def _detail(ruleset: Ruleset, is_active: bool) -> RulesetDetail:
    return RulesetDetail(
        id=ruleset.id,
        name=ruleset.name,
        description=ruleset.description,
        version=ruleset.version,
        source=ruleset.source,
        is_active=is_active,
        prompt_sections=ruleset.prompt_sections,
        tool_policy=ruleset.tool_policy,
        model=ruleset.model,
        model_settings=ruleset.model_settings,
    )


async def _active_id() -> str:
    return (await rulesets.active_ruleset()).id


@router.get("/rulesets", response_model=list[RulesetSummary])
async def list_rulesets(user: AdminUser) -> list[RulesetSummary]:
    stored = await rulesets.list_rulesets()
    if stored:
        return [_summary(ruleset, is_active) for ruleset, is_active in stored]
    # Before the first seed, report what chat would actually use.
    active = await rulesets.active_ruleset()
    return [_summary(active, True)]


@router.get("/guardrails", response_model=GuardrailThresholds)
async def read_guardrails(user: AdminUser) -> GuardrailThresholds:
    return GuardrailThresholds(**vars(guardrails.current()))


@router.get("/rulesets/{ruleset_id}", response_model=RulesetDetail)
async def read_ruleset(ruleset_id: str, user: AdminUser) -> RulesetDetail:
    ruleset = await rulesets.get_ruleset(ruleset_id)
    if ruleset is None:
        try:
            ruleset = rulesets.packaged_ruleset(ruleset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Ruleset not found") from None
    return _detail(ruleset, ruleset.id == await _active_id())


@router.put("/rulesets/{ruleset_id}", response_model=RulesetDetail)
async def save_ruleset(ruleset_id: str, body: RulesetSave, user: AdminUser) -> RulesetDetail:
    if ruleset_id != body.id:
        raise HTTPException(status_code=400, detail="Ruleset id in the path and body must match")
    try:
        saved = await rulesets.save_ruleset(
            Ruleset(**body.model_dump(), source="custom"), created_by=user.email or user.id
        )
    except rulesets.PackagedRulesetIdError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _detail(saved, saved.id == await _active_id())


@router.post("/rulesets/{ruleset_id}/clone", response_model=RulesetDetail)
async def clone_ruleset(ruleset_id: str, body: RulesetSave, user: AdminUser) -> RulesetDetail:
    """Copy a ruleset to a new id, one version up.

    Cloning rather than editing in place keeps the wording that produced the
    transcripts already logged against the old version.
    """
    source = await rulesets.get_ruleset(ruleset_id)
    if source is None:
        try:
            source = rulesets.packaged_ruleset(ruleset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Ruleset not found") from None
    if await rulesets.get_ruleset(body.id) is not None:
        raise HTTPException(status_code=409, detail=f"A ruleset named {body.id!r} already exists")
    saved = await rulesets.save_ruleset(
        rulesets.next_version(source, body.id, body.name), created_by=user.email or user.id
    )
    return _detail(saved, False)


@router.post("/rulesets/{ruleset_id}/activate", response_model=RulesetSummary)
async def activate_ruleset(ruleset_id: str, user: AdminUser) -> RulesetSummary:
    try:
        await rulesets.activate_ruleset(ruleset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Ruleset not found") from None
    ruleset = await rulesets.get_ruleset(ruleset_id)
    assert ruleset is not None
    return _summary(ruleset, True)


@router.post("/rulesets/{ruleset_id}/preview", response_model=PreviewResult)
async def preview_ruleset(ruleset_id: str, body: PreviewRequest, user: AdminUser) -> PreviewResult:
    """The exact system prompt this ruleset produces, for one scope kind.

    Rendered through the same function the chat path uses, so an unresolved
    ``{{placeholder}}`` or a section left disabled is visible before activating.
    """
    if body.scope_kind not in _PREVIEW_SCOPE_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown scope kind: {body.scope_kind}")
    ruleset = await rulesets.get_ruleset(ruleset_id)
    if ruleset is None:
        try:
            ruleset = rulesets.packaged_ruleset(ruleset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Ruleset not found") from None
    scope = ChatScope(kind=body.scope_kind, key="preview", job_ids=[])
    instructions = _instructions_for_ruleset(ruleset, scope)
    return PreviewResult(
        scope_kind=body.scope_kind,
        instructions=instructions,
        character_count=len(instructions),
    )


@router.post("/compare")
async def compare_rulesets(body: CompareRequest, user: AdminUser) -> StreamingResponse:
    """Answer one message under two policies at once, merged onto one SSE stream.

    Every event carries a ``variant`` index, so "constrained vs raw" and "one
    ruleset, two models" are the same mechanism. The submit tools are withheld
    from both arms — see ``services.assistant_compare``.
    """
    await require_chat_available(user.id)
    variants = [
        assistant_compare.VariantSpec(ruleset_id=variant.ruleset_id, model=variant.model)
        for variant in body.variants
    ]
    try:
        comparison = await assistant_compare.prepare_comparison(
            user.id,
            variants,
            source_session_id=body.source_session_id,
            scope=body.scope,
        )
    except assistant_compare.UnknownRulesetError as exc:
        raise HTTPException(status_code=404, detail=f"Ruleset not found: {exc.args[0]}") from None
    except assistant_compare.UnknownSessionError:
        raise HTTPException(status_code=404, detail="Session not found") from None

    return StreamingResponse(
        assistant_compare.stream_comparison(comparison, user.id, body.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/comparisons/{comparison_id}/vote", response_model=VoteOut)
async def vote_on_comparison(comparison_id: str, body: VoteIn, user: AdminUser) -> VoteOut:
    """Record which arm won, on both arms' turn logs."""
    try:
        rated = await assistant_compare.record_vote(
            comparison_id, user.id, body.winner_session_id, body.note
        )
    except assistant_compare.UnknownSessionError:
        raise HTTPException(status_code=404, detail="Comparison not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return VoteOut(rated_turns=rated)


@router.delete("/comparisons/{comparison_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comparison(comparison_id: str, user: AdminUser) -> None:
    """Discard a comparison's scratch sessions."""
    if not await assistant_compare.delete_comparison(comparison_id, user.id):
        raise HTTPException(status_code=404, detail="Comparison not found")
