"""Side-by-side ruleset comparison.

The question this exists to answer is empirical: does a wording change actually
make the assistant better? Two answers to the same message, produced under two
policies (or two models under one policy), with a vote recorded against both
turn logs, is the cheapest way to find out.

Each variant runs in its own **cloned scratch session**. Cloning rather than
sharing one session is what lets the config-mutating tools work: both variants
can patch a benchmark or blend configuration and be compared on what they
proposed, without either overwriting the other or touching the source session.

``submit_benchmark`` and ``submit_blend`` are withheld from every variant. The
playground is for comparing what the assistant *says and proposes*, and a
comparison that could launch two jobs would make running one an experimental
cost. The guardrails are unaffected by that denial — they are enforced at the
submission chokepoint in code, so the reason nothing runs here is the missing
tool, not a prompt asking nicely.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from ai_almanac.server.db import get_db
from ai_almanac.server.services import rulesets
from ai_almanac.server.services.chat_artifacts import delete_chat_figure_artifact
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.chat_turns import stream_chat_turn
from ai_almanac.server.services.rulesets import Ruleset, ToolPolicy

logger = logging.getLogger(__name__)

# Withheld from every comparison variant, at registration, so there is no schema
# entry for the model to attempt.
COMPARE_DENIED_TOOLS = ("submit_benchmark", "submit_blend")


class UnknownRulesetError(KeyError):
    """A variant named a ruleset that neither the DB nor the package has."""


class UnknownSessionError(KeyError):
    """The source session to clone does not exist for this user."""


@dataclass(frozen=True)
class VariantSpec:
    """One arm of a comparison: a ruleset, optionally with the model overridden."""

    ruleset_id: str
    model: str | None = None


@dataclass(frozen=True)
class PreparedVariant:
    index: int
    session_id: str
    ruleset: Ruleset


@dataclass(frozen=True)
class Comparison:
    id: str
    variants: tuple[PreparedVariant, ...]


def _now() -> datetime:
    return datetime.now(UTC)


def _sse(event_type: str, **payload: object) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def _tagged(event: str, variant: int) -> str:
    """Re-frame one variant's SSE event with the column it belongs to.

    ``stream_chat_turn`` already emits framed JSON, so this only adds the tag —
    every event type the chat UI understands stays understood, which is what
    lets the comparison view reuse the same rendering.
    """
    payload = json.loads(event.removeprefix("data: ").strip())
    payload["variant"] = variant
    return f"data: {json.dumps(payload)}\n\n"


async def variant_ruleset(variant: VariantSpec) -> Ruleset:
    """The ruleset a variant runs under, with the submit tools withheld."""
    ruleset = await rulesets.get_ruleset(variant.ruleset_id)
    if ruleset is None:
        try:
            ruleset = rulesets.packaged_ruleset(variant.ruleset_id)
        except KeyError:
            raise UnknownRulesetError(variant.ruleset_id) from None
    return ruleset.model_copy(
        update={
            "tool_policy": ToolPolicy(
                deny=sorted({*ruleset.tool_policy.deny, *COMPARE_DENIED_TOOLS})
            ),
            "model": variant.model or ruleset.model,
        }
    )


async def _clone_session(
    conn, user_id: str, source_session_id: str, comparison_id: str, title: str
) -> str:
    session_id = str(uuid4())
    result = await conn.execute(
        sa.text("""
            INSERT INTO chat_sessions
                (id, user_id, title, provider_state, scope, transcript, benchmark_config,
                 benchmark_validation, blend_config, blend_validation, run_id,
                 comparison_id, created_at, updated_at)
            SELECT :id, user_id, :title, provider_state, scope, transcript, benchmark_config,
                   benchmark_validation, blend_config, blend_validation, run_id,
                   :comparison_id, :now, :now
            FROM chat_sessions
            WHERE id = :source_id AND user_id = :uid
        """),
        {
            "id": session_id,
            "title": title,
            "comparison_id": comparison_id,
            "now": _now(),
            "source_id": source_session_id,
            "uid": user_id,
        },
    )
    if result.rowcount == 0:
        raise UnknownSessionError(source_session_id)
    return session_id


async def _new_session(conn, user_id: str, scope: ChatScope, comparison_id: str, title: str) -> str:
    session_id = str(uuid4())
    await conn.execute(
        sa.text("""
            INSERT INTO chat_sessions
                (id, user_id, title, provider_state, scope, transcript,
                 comparison_id, created_at, updated_at)
            VALUES (:id, :uid, :title, '[]', :scope, '[]', :comparison_id, :now, :now)
        """),
        {
            "id": session_id,
            "uid": user_id,
            "title": title,
            "scope": json.dumps(scope.model_dump(mode="json")),
            "comparison_id": comparison_id,
            "now": _now(),
        },
    )
    return session_id


async def prepare_comparison(
    user_id: str,
    variants: Sequence[VariantSpec],
    *,
    source_session_id: str | None = None,
    scope: ChatScope | None = None,
    blind: bool = False,
) -> Comparison:
    """Resolve the rulesets and create one scratch session per variant.

    Separate from the streaming so a bad ruleset id or an unreachable source
    session is an HTTP error, not an event half way down a stream the client has
    already started rendering.

    ``blind`` keeps ruleset names out of the scratch-session titles — the owner
    can fetch those sessions by id, so a named title would unblind the arms.
    """
    comparison_id = str(uuid4())
    resolved = [await variant_ruleset(variant) for variant in variants]
    prepared: list[PreparedVariant] = []
    async with get_db() as conn:
        for index, ruleset in enumerate(resolved):
            label = f"Arm {index + 1}" if blind else ruleset.name
            title = f"Comparison {comparison_id[:8]} · {label}"
            session_id = (
                await _clone_session(conn, user_id, source_session_id, comparison_id, title)
                if source_session_id
                else await _new_session(
                    conn,
                    user_id,
                    scope or ChatScope(kind="benchmark_setup", key=comparison_id, job_ids=[]),
                    comparison_id,
                    title,
                )
            )
            prepared.append(PreparedVariant(index=index, session_id=session_id, ruleset=ruleset))
    return Comparison(id=comparison_id, variants=tuple(prepared))


async def _merged(streams: Sequence[AsyncIterator[str]]) -> AsyncIterator[tuple[int, str]]:
    """Interleave the variant streams, tagging each event with its index.

    A queue rather than round-robin polling: the point of running the variants
    concurrently is that a slow one does not hold up the other's tokens.
    """
    queue: asyncio.Queue[tuple[int, str | None]] = asyncio.Queue()

    async def pump(index: int, stream: AsyncIterator[str]) -> None:
        try:
            async for event in stream:
                await queue.put((index, event))
        except Exception as exc:
            logger.exception("comparison variant %s failed", index)
            await queue.put(
                (index, _sse("error", error_type="internal_error", message=str(exc) or "failed"))
            )
        finally:
            await queue.put((index, None))

    tasks = [asyncio.create_task(pump(index, stream)) for index, stream in enumerate(streams)]
    try:
        live = len(tasks)
        while live:
            index, event = await queue.get()
            if event is None:
                live -= 1
                continue
            yield index, event
    finally:
        for task in tasks:
            task.cancel()


def _variant_intro(variant: PreparedVariant, *, reveal: bool) -> dict:
    intro = {"variant": variant.index, "session_id": variant.session_id}
    if reveal:
        intro |= {
            "ruleset_id": variant.ruleset.id,
            "ruleset_name": variant.ruleset.name,
            "ruleset_version": variant.ruleset.version,
            "model": variant.ruleset.model,
        }
    return intro


async def stream_comparison(
    comparison: Comparison, user_id: str, message: str, *, reveal: bool = True
) -> AsyncIterator[str]:
    """Run every variant on the same message, merged onto one SSE stream.

    ``reveal=False`` is the blind mode: the stream identifies arms only by
    index and session id, and the vote response is where names come out.
    """
    yield _sse(
        "comparison_started",
        comparison_id=comparison.id,
        variants=[_variant_intro(variant, reveal=reveal) for variant in comparison.variants],
    )
    streams = [
        stream_chat_turn(
            variant.session_id,
            user_id,
            message,
            None,
            active_ruleset=variant.ruleset,
            comparison_id=comparison.id,
        )
        for variant in comparison.variants
    ]
    async for index, event in _merged(streams):
        yield _tagged(event, index)
    yield _sse("comparison_complete", comparison_id=comparison.id)


async def comparison_session_ids(comparison_id: str, user_id: str) -> list[str]:
    async with get_db() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT id FROM chat_sessions "
                    "WHERE comparison_id = :cid AND user_id = :uid ORDER BY created_at"
                ),
                {"cid": comparison_id, "uid": user_id},
            )
        ).fetchall()
    return [row[0] for row in rows]


async def comparison_arms(comparison_id: str, user_id: str) -> list[dict]:
    """Which ruleset ran in each arm, from the turn logs — the blind-mode reveal.

    Derived rather than stored: the turn log already records provenance per
    session, keyed by comparison_id. A turn whose log write was swallowed shows
    up as an absent row, which the caller renders as "unknown" rather than
    failing the vote.
    """
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.text("""
                        SELECT DISTINCT session_id, ruleset_id, ruleset_version
                        FROM assistant_turn_logs
                        WHERE comparison_id = :cid AND user_id = :uid
                    """),
                    {"cid": comparison_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchall()
        )
    return [dict(row) for row in rows]


async def record_vote(
    comparison_id: str,
    user_id: str,
    winner_session_id: str | None,
    note: str | None = None,
) -> int:
    """Score both arms of a comparison. ``None`` winner records a tie.

    The vote lands on ``assistant_turn_logs``, the same column a thumbs-up in the
    chat writes, so a comparison and an in-conversation rating aggregate together
    per ruleset version.
    """
    sessions = await comparison_session_ids(comparison_id, user_id)
    if not sessions:
        raise UnknownSessionError(comparison_id)
    if winner_session_id is not None and winner_session_id not in sessions:
        raise ValueError(f"{winner_session_id!r} is not part of this comparison")

    async with get_db() as conn:
        result = await conn.execute(
            sa.text("""
                UPDATE assistant_turn_logs
                SET rating = CASE
                        WHEN :winner IS NULL THEN 0
                        WHEN session_id = :winner THEN 1
                        ELSE -1
                    END,
                    rating_note = :note
                WHERE comparison_id = :cid AND user_id = :uid
            """),
            {"winner": winner_session_id, "note": note, "cid": comparison_id, "uid": user_id},
        )
    return result.rowcount


async def delete_comparison(comparison_id: str, user_id: str) -> int:
    """Drop a comparison's scratch sessions and any figures they produced."""
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.text("""
                        SELECT chat_artifacts.storage_key
                        FROM chat_sessions
                        LEFT JOIN chat_artifacts
                            ON chat_artifacts.session_id = chat_sessions.id
                        WHERE chat_sessions.comparison_id = :cid
                          AND chat_sessions.user_id = :uid
                    """),
                    {"cid": comparison_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchall()
        )
        for storage_key in [row["storage_key"] for row in rows if row.get("storage_key")]:
            await delete_chat_figure_artifact(storage_key)
        result = await conn.execute(
            sa.text("DELETE FROM chat_sessions WHERE comparison_id = :cid AND user_id = :uid"),
            {"cid": comparison_id, "uid": user_id},
        )
    return result.rowcount
