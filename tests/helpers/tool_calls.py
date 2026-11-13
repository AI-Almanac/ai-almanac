"""Calling the assistant's workspace tools directly, as one turn of a conversation would."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic_ai import ApprovalRequired

from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.llm import ChatDeps
from ai_almanac.server.services.turn_log import TurnRecord


def tool_ctx(user_id: str, session_id: str, workspace_id: str, approved: bool = False):
    """The run context a tool sees during turn ``t1`` of ``session_id`` in a workspace."""
    turn = TurnRecord(session_id=session_id, user_id=user_id, turn_id="t1", model_name="test-model")
    scope = ChatScope(kind="workspace", key=workspace_id, job_ids=[])
    return SimpleNamespace(
        deps=ChatDeps(
            user_id=user_id,
            session_id=session_id,
            scope=scope,
            turn=turn,
            workspace_id=workspace_id,
        ),
        tool_call_approved=approved,
        tool_call_metadata=None,
    )


async def approve(tool, ctx, **kwargs) -> dict:
    """``tool`` called as the user approving the card it asks for. A call refused before
    any card is shown returns its refusal."""
    try:
        return await tool(ctx, **kwargs)
    except ApprovalRequired as asked:
        approved = SimpleNamespace(
            deps=ctx.deps,
            tool_call_approved=True,
            tool_call_metadata={"approved_params": asked.metadata["params"]},
        )
        return await tool(approved, **kwargs)
