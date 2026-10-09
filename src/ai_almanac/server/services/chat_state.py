from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .benchmark_state import BenchmarkScope


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
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Stable rule ids behind the messages, so the UI and the turn log can key on
    # the rule rather than on its wording.
    finding_keys: list[str] = Field(default_factory=list)


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


class ChatTurn(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    # All of the turn's text, concatenated: what history, ratings, and the turn
    # log read. `blocks` is the same turn in the order it happened, for display.
    content: str = ""
    created_at: datetime
    status: Literal["streaming", "completed", "failed"] = "completed"
    error: str | None = None
    tool_calls: list[ChatToolCall] = Field(default_factory=list)
    artifacts: list[ChatArtifact] = Field(default_factory=list)
    guardrails: list[GuardrailNotice] = Field(default_factory=list)
    blocks: list[TurnBlock] = Field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_turn_id() -> str:
    return str(uuid4())
