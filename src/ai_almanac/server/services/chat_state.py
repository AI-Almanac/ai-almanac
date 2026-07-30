from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
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
    status: Literal["running", "completed", "failed"] = "completed"
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


class ChatTurn(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str = ""
    created_at: datetime
    status: Literal["streaming", "completed", "failed"] = "completed"
    error: str | None = None
    tool_calls: list[ChatToolCall] = Field(default_factory=list)
    artifacts: list[ChatArtifact] = Field(default_factory=list)
    guardrails: list[GuardrailNotice] = Field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_turn_id() -> str:
    return str(uuid4())
