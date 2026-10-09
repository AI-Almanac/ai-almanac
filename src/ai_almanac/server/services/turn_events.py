"""Fold chat stream events into a ChatTurn.

One reducer for every place that builds a turn from the stream — the LLM
service as it emits, and the turn lifecycle as it relays — so the turn a user
watched stream is the turn that is persisted, in the same order.
"""

from __future__ import annotations

from collections.abc import Callable

from .chat_state import (
    ChatArtifact,
    ChatToolCall,
    ChatTurn,
    GuardrailNotice,
    TextBlock,
    ThinkingBlock,
    ToolBlock,
)

_PARAGRAPH_BREAK = "\n\n"


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _find_tool_call(turn: ChatTurn, tool_call_id: object) -> ChatToolCall | None:
    return next((call for call in turn.tool_calls if call.id == tool_call_id), None)


def _paragraph_break_needed(turn: ChatTurn) -> bool:
    """Text resuming after a tool call starts a new paragraph in `content`."""
    resumes_after_other_block = bool(turn.blocks) and turn.blocks[-1].kind != "text"
    return resumes_after_other_block and bool(turn.content) and not turn.content[-1].isspace()


def _on_text_delta(turn: ChatTurn, data: dict) -> None:
    content = data.get("content") or ""
    if not content:
        return
    if _paragraph_break_needed(turn):
        turn.content += _PARAGRAPH_BREAK
    turn.content += content
    last = turn.blocks[-1] if turn.blocks else None
    if isinstance(last, TextBlock):
        last.text += content
    else:
        turn.blocks.append(TextBlock(text=content))


def _on_thinking_delta(turn: ChatTurn, data: dict) -> None:
    content = data.get("content") or ""
    if not content:
        return
    last = turn.blocks[-1] if turn.blocks else None
    if isinstance(last, ThinkingBlock):
        last.text += content
    else:
        turn.blocks.append(ThinkingBlock(text=content))


def _on_tool_call(turn: ChatTurn, data: dict) -> None:
    """Add a tool call, or advance one first announced while its args streamed."""
    payload = data.get("tool_call")
    if not isinstance(payload, dict):
        return
    incoming = ChatToolCall.model_validate(payload)
    existing = _find_tool_call(turn, incoming.id)
    if existing is None:
        turn.tool_calls.append(incoming)
        turn.blocks.append(ToolBlock(tool_call_id=incoming.id))
        return
    existing.name = incoming.name
    existing.status = incoming.status
    existing.input = incoming.input or existing.input


def _on_tool_result(turn: ChatTurn, data: dict) -> None:
    tool_call = _find_tool_call(turn, data.get("tool_call_id"))
    if tool_call is None:
        return
    tool_call.status = data.get("status", tool_call.status)
    tool_call.result = data.get("result")


def _on_artifact(turn: ChatTurn, data: dict) -> None:
    payload = data.get("artifact")
    if not isinstance(payload, dict):
        return
    artifact = ChatArtifact.model_validate(payload)
    if not any(existing.id == artifact.id for existing in turn.artifacts):
        turn.artifacts.append(artifact)
    tool_call = _find_tool_call(turn, data.get("tool_call_id"))
    if tool_call is not None and not any(a.id == artifact.id for a in tool_call.artifacts):
        tool_call.artifacts.append(artifact)


def _on_guardrail(turn: ChatTurn, data: dict) -> None:
    turn.guardrails.append(
        GuardrailNotice(
            tool_call_id=data.get("tool_call_id"),
            errors=_strings(data.get("errors")),
            warnings=_strings(data.get("warnings")),
            finding_keys=_strings(data.get("finding_keys")),
        )
    )


_HANDLERS: dict[str, Callable[[ChatTurn, dict], None]] = {
    "text_delta": _on_text_delta,
    "thinking_delta": _on_thinking_delta,
    "tool_call": _on_tool_call,
    "tool_result": _on_tool_result,
    "artifact": _on_artifact,
    "guardrail": _on_guardrail,
}


def apply_stream_event(turn: ChatTurn, data: dict) -> None:
    """Fold one stream event into the turn; events that carry no turn content are ignored."""
    handler = _HANDLERS.get(data.get("type", ""))
    if handler is not None:
        handler(turn, data)
