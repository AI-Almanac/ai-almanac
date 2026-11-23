"""The one way a tool waits for the user's approval before it acts.

A gated call is proposed twice: once to ask, and again when the user's decision
resumes it. The first time it raises ``ApprovalRequired`` with its ``params`` and
what its card shows. The second time it runs only if its params are still the ones
the card showed, which the resumed call carries as ``approved_params``: a step whose
inputs moved after the card was shown, or whose approval does not say what it
approved, is refused rather than run.

The user decides one card at a time, but a reply resumes only once every call it
paused on has an answer. The calls not being decided are answered ``ASK_AGAIN``,
which pauses them once more, each with a card of its own.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from pydantic_ai import ApprovalRequired, RunContext

NOT_SHOWN = (
    "This step was not run: its approval did not say what was approved, so it cannot be "
    "checked against what would run now. Propose it again."
)

# Sent as approved because only an approval makes pydantic-ai call the tool again; the
# marker in its metadata is what keeps the gate shut.
ASK_AGAIN_METADATA = {"ask_again": True}


async def gated(
    ctx: RunContext,
    tool: str,
    params: dict,
    *,
    card: Callable[[], Awaitable[dict]],
    run: Callable[[], Awaitable[dict]],
    moved: dict,
) -> dict:
    """Ask for approval of ``params``, or ``run`` once the approval covers exactly them.

    ``card`` builds what the approval card shows beside the params; it is only built
    when approval is asked for. ``moved`` is the result when the params changed since.
    """
    if _awaiting_decision(ctx):
        raise ApprovalRequired(
            metadata={"approval_required": True, "tool": tool, "params": params, **await card()}
        )
    approved = (ctx.tool_call_metadata or {}).get("approved_params")
    if approved is None:
        return {"error": NOT_SHOWN}
    if _canonical(approved) != _canonical(params):
        return moved
    return await run()


def _awaiting_decision(ctx: RunContext) -> bool:
    asked_again = (ctx.tool_call_metadata or {}).get("ask_again", False)
    return not ctx.tool_call_approved or asked_again


def _canonical(params: object) -> str:
    return json.dumps(params, sort_keys=True, default=str)
