"""Per-turn assistant telemetry and the compliance flags derived from it.

The point of recording a turn is to answer, with data rather than conviction,
whether a ruleset change made the assistant better. So every turn is stored
against the ruleset version and model that produced it, together with the tools
it called, the guardrails that fired, and three derived flags.

The flags **measure**; they never enforce. The user is warned by the guardrail
banner regardless (see ``services.guardrails``), and a flag being wrong costs a
slightly noisy metric rather than a missing caution. That is why they can afford
to be cheap regexes, and why none of them is surfaced in the chat UI yet — the
false-positive rate has to be known first.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from ai_almanac.server.services.guardrails import ACKNOWLEDGEMENT_TERMS

logger = logging.getLogger(__name__)

# Tools whose results carry the figures an answer would quote. Calling one of
# these is what distinguishes "read a number" from "recalled a number".
# `list_*` tools are excluded on purpose: they return names, not measurements.
DATA_READING_TOOLS = frozenset(
    {
        "get_job_metrics",
        "get_skill_scores",
        "get_spatial_summary",
        "get_blend_results",
        "get_job_info",
        "get_job_logs",
        "run_code",
        "run_code_sandbox",
    }
)

# Only decimals count as a quoted measurement. Integers are what the domain
# rules themselves are written in ("under 10 test years", "MAE under 5 days"),
# and the prompt actively encourages quoting those from memory, so counting them
# would flag every correct conceptual answer.
_DECIMAL = re.compile(r"\d+\.\d+")

_MAP_DETAIL = re.compile(
    r"grid[- ]?(?:point|cell)|per[- ]grid|this cell|that cell|these cells", re.IGNORECASE
)


@dataclass
class TurnRecord:
    """Accumulated during one streamed turn, written once at the end."""

    session_id: str
    user_id: str
    turn_id: str = ""
    scope_kind: str | None = None
    ruleset_id: str | None = None
    ruleset_version: int | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    requests: int | None = None
    failure_category: str | None = None
    comparison_id: str | None = None
    text: str = ""
    tool_calls: list[str] = field(default_factory=list)
    guardrail_keys: list[str] = field(default_factory=list)


def compute_flags(record: TurnRecord) -> dict[str, bool]:
    """The three behaviours worth counting across rulesets.

    ``numbers_without_tool_call`` — the answer quotes a decimal but the turn read
    no data. The proxy for the assistant doing arithmetic in its head instead of
    calling a tool. It cannot distinguish a recalled decimal from an invented
    one, so read it as a rate to compare between rulesets, not as a per-turn
    verdict.

    ``guardrail_unacknowledged`` — a guardrail fired and the answer never engaged
    with it. The user still saw the banner; this measures whether the assistant
    also explained it, which is exactly what a ruleset is supposed to improve.

    ``map_narration`` — the answer walks through grid-point detail on a sample
    the platform flagged as too small to support it.
    """
    text = record.text
    lowered = text.lower()
    called = set(record.tool_calls)
    fired = set(record.guardrail_keys)

    unacknowledged = any(
        not any(term in lowered for term in ACKNOWLEDGEMENT_TERMS.get(key, ()))
        for key in fired
        if key in ACKNOWLEDGEMENT_TERMS
    )

    return {
        "numbers_without_tool_call": bool(_DECIMAL.search(text))
        and not (called & DATA_READING_TOOLS),
        "guardrail_unacknowledged": unacknowledged,
        "map_narration": bool(_MAP_DETAIL.search(text)) and "small_test_sample" in fired,
    }


async def record_turn(record: TurnRecord) -> None:
    """Persist one turn's telemetry. Never raises.

    Telemetry must not be able to fail a conversation the user already received,
    so a write error is logged and dropped rather than surfaced.
    """
    if not record.turn_id:
        return
    from ai_almanac.server.db import get_db

    payload = {
        "id": str(uuid4()),
        "session_id": record.session_id,
        "user_id": record.user_id,
        "turn_id": record.turn_id,
        "created_at": datetime.now(UTC).isoformat(),
        "ruleset_id": record.ruleset_id,
        "ruleset_version": record.ruleset_version,
        "model_name": record.model_name,
        "scope_kind": record.scope_kind,
        "latency_ms": record.latency_ms,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "requests": record.requests,
        "failure_category": record.failure_category,
        "tool_calls": json.dumps(record.tool_calls),
        "guardrail_keys": json.dumps(record.guardrail_keys),
        "flags": json.dumps(compute_flags(record)),
        "comparison_id": record.comparison_id,
    }
    try:
        async with get_db() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO assistant_turn_logs "
                    "(id, session_id, user_id, turn_id, created_at, ruleset_id, "
                    " ruleset_version, model_name, scope_kind, latency_ms, input_tokens, "
                    " output_tokens, requests, failure_category, tool_calls, guardrail_keys, "
                    " flags, comparison_id) "
                    "VALUES (:id, :session_id, :user_id, :turn_id, :created_at, :ruleset_id, "
                    " :ruleset_version, :model_name, :scope_kind, :latency_ms, :input_tokens, "
                    " :output_tokens, :requests, :failure_category, :tool_calls, :guardrail_keys, "
                    " :flags, :comparison_id)"
                ),
                payload,
            )
    except SQLAlchemyError:
        logger.exception("assistant turn log write failed for session %s", record.session_id)


async def rate_turn(
    session_id: str, turn_id: str, user_id: str, value: int, note: str | None
) -> bool:
    """Attach a rating to a logged turn. False when there is no such turn.

    Scoped by ``user_id`` so a rating can only land on the rater's own
    conversation.
    """
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        result = await conn.execute(
            sa.text(
                "UPDATE assistant_turn_logs SET rating = :rating, rating_note = :note "
                "WHERE session_id = :session_id AND turn_id = :turn_id AND user_id = :user_id"
            ),
            {
                "rating": value,
                "note": note,
                "session_id": session_id,
                "turn_id": turn_id,
                "user_id": user_id,
            },
        )
    return result.rowcount > 0


async def feedback_summary() -> list[dict]:
    """Votes, ratings and flags rolled up per ruleset version.

    Aggregated in Python rather than SQL: ``flags`` is JSON text and boolean
    extraction differs between SQLite and Postgres, and turn-log volume is
    tiny.
    """
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT ruleset_id, ruleset_version, rating, comparison_id, flags "
                        "FROM assistant_turn_logs WHERE ruleset_id IS NOT NULL"
                    )
                )
            )
            .mappings()
            .fetchall()
        )

    groups: dict[tuple[str, int | None], dict] = {}
    for row in rows:
        key = (row["ruleset_id"], row["ruleset_version"])
        group = groups.setdefault(
            key,
            {
                "ruleset_id": row["ruleset_id"],
                "ruleset_version": row["ruleset_version"],
                "turns": 0,
                "rated": 0,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "flag_counts": {},
            },
        )
        group["turns"] += 1
        rating = row["rating"]
        if rating is not None:
            group["rated"] += 1
            if rating > 0:
                group["wins"] += 1
            elif rating < 0:
                group["losses"] += 1
            elif row["comparison_id"] is not None:
                group["ties"] += 1
        flags = row["flags"]
        flags = json.loads(flags) if isinstance(flags, str) else (flags or {})
        for flag, value in flags.items():
            if value:
                group["flag_counts"][flag] = group["flag_counts"].get(flag, 0) + 1
    return sorted(groups.values(), key=lambda g: (g["ruleset_id"], g["ruleset_version"] or 0))
