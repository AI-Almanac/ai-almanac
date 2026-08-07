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

import random

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai_almanac.server.auth import AdminUser, CurrentUser, require_assistant_comparisons
from ai_almanac.server.services import assistant_compare, guardrails, rulesets, turn_log
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.llm import _instructions_for_ruleset
from ai_almanac.server.services.rulesets import PromptSection, Ruleset, ToolPolicy
from ai_almanac.settings import settings

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
    comparison_enabled: bool = False
    admin_enabled: bool = False
    activatable: bool = True
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
    activatable: bool = True
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


class BlindCompareRequest(BaseModel):
    """A user-triggered comparison of two exposed rulesets.

    The user picks the pair; the server shuffles which column is which, so the
    vote is still cast without knowing which answer came from which ruleset.
    Only exposed rulesets are eligible — a user cannot name a draft, archived,
    or admin-preview ruleset here; an admin's preview set is additionally in.
    """

    message: str = Field(min_length=1, max_length=8000)
    ruleset_ids: list[str] = Field(min_length=2, max_length=2)
    source_session_id: str | None = None
    scope: ChatScope | None = None


class VoteIn(BaseModel):
    """Which arm won. ``None`` records a tie."""

    winner_session_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class RevealedArm(BaseModel):
    """One arm's identity, disclosed after the vote. Nulls mean the turn log
    for that arm never landed — degraded, not an error."""

    session_id: str
    ruleset_id: str | None = None
    ruleset_name: str | None = None
    ruleset_version: int | None = None


class VoteOut(BaseModel):
    rated_turns: int
    arms: list[RevealedArm] = []


class RulesetOption(BaseModel):
    """What a non-admin may know about a ruleset: enough to pick one, nothing
    about its prompt or tool policy."""

    id: str
    name: str
    description: str
    is_active: bool
    # Visible only because the requester is an admin previewing it; lets the
    # picker badge it and the view-as-user mode hide it.
    admin_only: bool = False


class RulesetOptionsOut(BaseModel):
    rulesets: list[RulesetOption]
    # Whether the feature is switched on at all, and separately whether a
    # comparison can actually run (needs two exposed rulesets).
    comparisons_enabled: bool
    compare_available: bool


class RulesetFeedback(BaseModel):
    ruleset_id: str
    ruleset_version: int | None
    turns: int
    rated: int
    wins: int
    losses: int
    ties: int
    flag_counts: dict[str, int]


class PreviewRequest(BaseModel):
    scope_kind: str = "blend_setup"


class PreviewResult(BaseModel):
    scope_kind: str
    instructions: str
    character_count: int


def _summary(
    ruleset: Ruleset,
    is_active: bool,
    comparison_enabled: bool = False,
    admin_enabled: bool = False,
) -> RulesetSummary:
    return RulesetSummary(
        id=ruleset.id,
        name=ruleset.name,
        description=ruleset.description,
        version=ruleset.version,
        source=ruleset.source,
        is_active=is_active,
        comparison_enabled=comparison_enabled,
        admin_enabled=admin_enabled,
        activatable=ruleset.activatable,
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
        activatable=ruleset.activatable,
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
        return [
            _summary(row.ruleset, row.is_active, row.comparison_enabled, row.admin_enabled)
            for row in stored
        ]
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
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail="This ruleset is a comparison control and cannot be made active",
        ) from None
    ruleset = await rulesets.get_ruleset(ruleset_id)
    assert ruleset is not None
    return _summary(ruleset, True)


class ComparisonEnabledIn(BaseModel):
    enabled: bool


@router.post("/rulesets/{ruleset_id}/comparison-enabled", response_model=RulesetSummary)
async def set_comparison_enabled(
    ruleset_id: str, body: ComparisonEnabledIn, user: AdminUser
) -> RulesetSummary:
    """Expose or hide a ruleset for users — the style picker and comparison
    arms both draw from the exposed set."""
    try:
        await rulesets.set_comparison_enabled(ruleset_id, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="Ruleset not found") from None
    return await _stored_summary(ruleset_id)


@router.post("/rulesets/{ruleset_id}/admin-enabled", response_model=RulesetSummary)
async def set_admin_enabled(
    ruleset_id: str, body: ComparisonEnabledIn, user: AdminUser
) -> RulesetSummary:
    """Expose or hide a ruleset for admins only, so it can be pinned to real
    sessions and compared before any user sees it."""
    try:
        await rulesets.set_admin_enabled(ruleset_id, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="Ruleset not found") from None
    return await _stored_summary(ruleset_id)


async def _stored_summary(ruleset_id: str) -> RulesetSummary:
    stored = next(row for row in await rulesets.list_rulesets() if row.ruleset.id == ruleset_id)
    return _summary(
        stored.ruleset, stored.is_active, stored.comparison_enabled, stored.admin_enabled
    )


@router.delete("/rulesets/{ruleset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ruleset(ruleset_id: str, user: AdminUser) -> None:
    """Archive a custom ruleset: gone from every list, provenance kept.

    Packaged rulesets are refused — reseeding would resurrect them on the next
    startup, so a delete would look like it worked and then undo itself. The
    active ruleset is refused because chat must always resolve one.
    """
    try:
        rulesets.packaged_ruleset(ruleset_id)
    except KeyError:
        pass
    else:
        raise HTTPException(
            status_code=409, detail="Packaged rulesets ship with the app and cannot be deleted"
        )
    if ruleset_id == await _active_id():
        raise HTTPException(status_code=409, detail="Deactivate this ruleset before deleting it")
    try:
        await rulesets.archive_ruleset(ruleset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Ruleset not found") from None


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


@router.post("/compare", dependencies=[Depends(require_assistant_comparisons)])
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


@router.get("/ruleset-options", response_model=RulesetOptionsOut)
async def list_ruleset_options(user: CurrentUser) -> RulesetOptionsOut:
    """The rulesets an admin has exposed to users, for the style picker and
    the comparison-pair choice.

    Not gated on the comparison flag: the style picker outlives comparisons.
    The flag is reported instead, so the chat hides the comparison surface
    without a second request.
    """
    visible = [
        row
        for row in await rulesets.list_rulesets()
        if row.comparison_enabled or (user.is_admin and row.admin_enabled)
    ]
    comparisons_enabled = settings.comparisons_allowed(user.is_admin)
    return RulesetOptionsOut(
        rulesets=[
            RulesetOption(
                id=row.ruleset.id,
                name=row.ruleset.name,
                description=row.ruleset.description,
                is_active=row.is_active,
                admin_only=not row.comparison_enabled,
            )
            for row in visible
        ],
        comparisons_enabled=comparisons_enabled,
        compare_available=comparisons_enabled and len(visible) >= 2,
    )


@router.post("/compare/blind", dependencies=[Depends(require_assistant_comparisons)])
async def compare_blind(body: BlindCompareRequest, user: CurrentUser) -> StreamingResponse:
    """Answer one message under two user-chosen rulesets, columns blinded.

    The user picks the pair from the exposed rulesets; the server shuffles
    which column is which, so the stream names arms only by index and the
    vote response is where the identities come out.
    """
    await require_chat_available(user.id)
    if len(set(body.ruleset_ids)) != 2:
        raise HTTPException(status_code=400, detail="Pick two different rulesets")
    for ruleset_id in body.ruleset_ids:
        if await rulesets.selectable_ruleset(ruleset_id, for_admin=user.is_admin) is None:
            raise HTTPException(status_code=400, detail=f"Ruleset not available: {ruleset_id}")
    arms = [assistant_compare.VariantSpec(ruleset_id=rid) for rid in body.ruleset_ids]
    random.shuffle(arms)
    try:
        comparison = await assistant_compare.prepare_comparison(
            user.id,
            arms,
            source_session_id=body.source_session_id,
            scope=body.scope,
            blind=True,
        )
    except assistant_compare.UnknownRulesetError as exc:
        raise HTTPException(status_code=404, detail=f"Ruleset not found: {exc.args[0]}") from None
    except assistant_compare.UnknownSessionError:
        raise HTTPException(status_code=404, detail="Session not found") from None

    return StreamingResponse(
        assistant_compare.stream_comparison(comparison, user.id, body.message, reveal=False),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ComparisonMessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


@router.post(
    "/comparisons/{comparison_id}/message", dependencies=[Depends(require_assistant_comparisons)]
)
async def continue_comparison(
    comparison_id: str, body: ComparisonMessageIn, user: CurrentUser
) -> StreamingResponse:
    """Run a follow-up message through both arms of a live comparison.

    Each arm continues its own scratch conversation under the ruleset that
    produced its earlier answers, so the side-by-side view is a dialogue, not a
    single exchange. Identity stays out of the stream either way: a labeled
    client already knows its arms, a blind one must not learn them.
    """
    await require_chat_available(user.id)
    try:
        comparison = await assistant_compare.resume_comparison(comparison_id, user.id)
    except assistant_compare.UnknownSessionError:
        raise HTTPException(status_code=404, detail="Comparison not found") from None
    except assistant_compare.UnknownRulesetError as exc:
        raise HTTPException(
            status_code=409, detail=f"Ruleset no longer exists: {exc.args[0]}"
        ) from None

    return StreamingResponse(
        assistant_compare.stream_comparison(comparison, user.id, body.message, reveal=False),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _revealed_arms(comparison_id: str, user_id: str) -> list[RevealedArm]:
    arms = []
    for arm in await assistant_compare.comparison_arms(comparison_id, user_id):
        ruleset = await rulesets.get_ruleset(arm["ruleset_id"]) if arm["ruleset_id"] else None
        arms.append(
            RevealedArm(
                session_id=arm["session_id"],
                ruleset_id=arm["ruleset_id"],
                ruleset_name=ruleset.name if ruleset else arm["ruleset_id"],
                ruleset_version=arm["ruleset_version"],
            )
        )
    return arms


@router.post(
    "/comparisons/{comparison_id}/vote",
    response_model=VoteOut,
    dependencies=[Depends(require_assistant_comparisons)],
)
async def vote_on_comparison(comparison_id: str, body: VoteIn, user: CurrentUser) -> VoteOut:
    """Record which arm won, on both arms' turn logs, and reveal the arms."""
    try:
        rated = await assistant_compare.record_vote(
            comparison_id, user.id, body.winner_session_id, body.note
        )
    except assistant_compare.UnknownSessionError:
        raise HTTPException(status_code=404, detail="Comparison not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return VoteOut(rated_turns=rated, arms=await _revealed_arms(comparison_id, user.id))


@router.delete(
    "/comparisons/{comparison_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_assistant_comparisons)],
)
async def delete_comparison(comparison_id: str, user: CurrentUser) -> None:
    """Discard a comparison's scratch sessions."""
    if not await assistant_compare.delete_comparison(comparison_id, user.id):
        raise HTTPException(status_code=404, detail="Comparison not found")


@router.get("/feedback", response_model=list[RulesetFeedback])
async def ruleset_feedback(user: AdminUser) -> list[RulesetFeedback]:
    """Votes, ratings and flags per ruleset version — the read side of the
    turn log, so collected feedback is actually visible."""
    return [RulesetFeedback(**group) for group in await turn_log.feedback_summary()]
