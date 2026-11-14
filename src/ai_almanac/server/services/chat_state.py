"""A chat session's stored state: the turns its transcript holds, and what the model
history says about whether a turn is still going or a step still waits on the user."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .benchmark_state import BenchmarkRunSpec, BenchmarkScope, BenchmarkValidation
from .blend_state import BlendRunSpec, BlendValidation


class ChatScope(BenchmarkScope):
    pass


class ChatArtifact(BaseModel):
    id: str
    kind: Literal["figure"] = "figure"
    url: str
    label: str | None = None
    filename: str | None = None
    media_type: str | None = None
    created_at: datetime
    # Set when the figure is also a workspace artifact, so it can open in the canvas.
    workspace_artifact_id: str | None = None


class ChatToolCall(BaseModel):
    id: str
    name: str
    # "pending" while the model is still writing the call's arguments, which for
    # a code tool can take longer than running it.
    status: Literal["pending", "running", "completed", "failed"] = "completed"
    input: dict = Field(default_factory=dict)
    result: Any = None
    artifacts: list[ChatArtifact] = Field(default_factory=list)


class GuardrailNotice(BaseModel):
    """Statistical findings the platform reported during a turn.

    Recorded on the turn rather than left to the assistant's prose so the
    caution is shown whatever the model chose to say, and so it survives a page
    reload. See ``services.guardrails`` for why enforcement lives in code.
    """

    tool_call_id: str | None = None
    # Which config the validation described; the latest notice per kind is the
    # current one. None on notices persisted before this was recorded.
    config_kind: Literal["benchmark", "blend"] | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Stable rule ids behind the messages, so the UI and the turn log can key on
    # the rule rather than on its wording.
    finding_keys: list[str] = Field(default_factory=list)


class SourceArtifact(BaseModel):
    """A workspace artifact a turn's tools read, as the workspace labels it."""

    id: str
    label: str


class TextBlock(BaseModel):
    kind: Literal["text"] = "text"
    text: str = ""


class ThinkingBlock(BaseModel):
    """Reasoning summary or between-tool progress note the model wrote."""

    kind: Literal["thinking"] = "thinking"
    text: str = ""


class ToolBlock(BaseModel):
    kind: Literal["tool"] = "tool"
    tool_call_id: str


TurnBlock = Annotated[TextBlock | ThinkingBlock | ToolBlock, Field(discriminator="kind")]


class BenchmarkApprovalRequest(BaseModel):
    """The assistant asking the user to approve a benchmark run, as it is streamed."""

    type: Literal["benchmark_approval_request"] = "benchmark_approval_request"
    turn_id: str
    tool_call_id: str
    config: BenchmarkRunSpec
    validation: BenchmarkValidation | None = None


class BlendApprovalRequest(BaseModel):
    """The assistant asking the user to approve training a blend, as it is streamed."""

    type: Literal["blend_approval_request"] = "blend_approval_request"
    turn_id: str
    tool_call_id: str
    config: BlendRunSpec
    validation: BlendValidation | None = None


class ToolApprovalRequest(BaseModel):
    """The assistant asking the user to approve a workspace step, as it is streamed.

    ``metadata`` carries the step's ``params`` and the ``display`` facts its card shows.
    """

    type: Literal["tool_approval_request"] = "tool_approval_request"
    turn_id: str
    tool_call_id: str
    tool_call: ChatToolCall
    metadata: dict = Field(default_factory=dict)


ApprovalRequest = Annotated[
    BenchmarkApprovalRequest | BlendApprovalRequest | ToolApprovalRequest,
    Field(discriminator="type"),
]


class ChatTurn(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    # Who started the turn: "user", or "follow_up:<id>" for a platform-written
    # continuation, which keeps role="user" because the model history needs a prompt.
    trigger: str = "user"
    # All of the turn's text, concatenated: what history, ratings, and the turn
    # log read. `blocks` is the same turn in the order it happened, for display.
    content: str = ""
    created_at: datetime
    status: Literal["streaming", "completed", "failed"] = "completed"
    error: str | None = None
    tool_calls: list[ChatToolCall] = Field(default_factory=list)
    artifacts: list[ChatArtifact] = Field(default_factory=list)
    guardrails: list[GuardrailNotice] = Field(default_factory=list)
    # On an assistant turn: the read set behind the answer (D-R8), workspace scope only.
    source_artifacts: list[SourceArtifact] = Field(default_factory=list)
    blocks: list[TurnBlock] = Field(default_factory=list)
    # Every approval this turn asked for, kept so an unanswered one can be shown
    # again when the conversation is reopened. Whether one is still unanswered is
    # the model history's call, not this list's: see ``unprocessed_tool_calls``.
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_turn_id() -> str:
    return str(uuid4())


# ponytail: a turn streaming longer than this is assumed dead (e.g. the server
# restarted mid-turn); a heartbeat on the streaming turn would make this exact.
# While a turn streams, its relay touches the session this often, so a turn
# whose server died stops counting as live soon after, on any instance.
TURN_HEARTBEAT_SECONDS = 30
STALE_TURN_AFTER = timedelta(minutes=2)


def turn_in_flight(
    transcript: list[dict], last_active: object, now: datetime | None = None
) -> bool:
    """Another turn is streaming on this session and its relay is still alive
    (P8: one writer per session). ``last_active`` is the session's updated_at."""
    if not transcript or transcript[-1].get("status") != "streaming":
        return False
    return (now or utc_now()) - _as_datetime(last_active) < STALE_TURN_AFTER


def _as_datetime(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def unprocessed_tool_calls(provider_state: list) -> list:
    """Tool calls the model made last that have no result yet: those awaiting approval.

    A call is answered once any later message carries a result for it, so a step that
    ran stays answered even when the reply after it was never written.
    """
    answered: set[str] = set()
    for message in reversed(provider_state):
        calls = getattr(message, "tool_calls", None)
        if calls is not None:
            return [call for call in calls if call.tool_call_id not in answered]
        answered.update(
            part.tool_call_id
            for part in getattr(message, "parts", [])
            if getattr(part, "tool_call_id", None)
        )
    return []


def parse_llm_event(event: str) -> dict | None:
    stripped = event.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM stream emitted a malformed event") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM stream emitted a non-object event")
    return parsed
