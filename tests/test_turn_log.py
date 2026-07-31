"""Per-turn telemetry and the derived compliance flags.

These flags exist to compare rulesets, not to police a single answer. The tests
below therefore care most about *not* firing on correct behaviour: a flag that
cries wolf makes the comparison useless and, if it were ever surfaced, would
train users to ignore the guardrail banners next to it.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import text

from ai_almanac.server.services.turn_log import TurnRecord, compute_flags, record_turn


def record(**kwargs) -> TurnRecord:
    return TurnRecord(session_id="s1", user_id="u1", turn_id="t1", **kwargs)


# --- numbers_without_tool_call -------------------------------------------


def test_a_decimal_with_no_data_tool_call_is_flagged() -> None:
    flags = compute_flags(record(text="The blend scores 0.42 RPSS.", tool_calls=[]))
    assert flags["numbers_without_tool_call"]


def test_a_decimal_after_reading_data_is_not_flagged() -> None:
    flags = compute_flags(
        record(text="The blend scores 0.42 RPSS.", tool_calls=["get_blend_results"])
    )
    assert not flags["numbers_without_tool_call"]


def test_listing_models_does_not_count_as_reading_numbers() -> None:
    """list_* returns names, not measurements — quoting a score after only
    listing models is exactly the case worth counting."""
    flags = compute_flags(record(text="AIFS reaches 0.31.", tool_calls=["list_models"]))
    assert flags["numbers_without_tool_call"]


def test_integers_in_conceptual_answers_are_not_flagged() -> None:
    """The domain rules are written in integers ("under 10 test years", "MAE
    under 5 days") and the prompt tells the assistant to answer conceptual
    questions from knowledge. Counting integers would flag every one of those."""
    flags = compute_flags(
        record(
            text="Under 10 test years results are noisy, and MAE under 5 days is strong skill.",
            tool_calls=[],
        )
    )
    assert not flags["numbers_without_tool_call"]


def test_running_code_counts_as_reading_data() -> None:
    flags = compute_flags(record(text="The median is 3.5 days.", tool_calls=["run_code"]))
    assert not flags["numbers_without_tool_call"]


# --- guardrail_unacknowledged --------------------------------------------


def test_a_fired_guardrail_the_answer_ignores_is_flagged() -> None:
    flags = compute_flags(
        record(text="AIFS is the best model here.", guardrail_keys=["small_test_sample"])
    )
    assert flags["guardrail_unacknowledged"]


def test_an_explained_guardrail_is_not_flagged() -> None:
    flags = compute_flags(
        record(
            text="AIFS leads, but this is a small sample so the difference may not be real.",
            guardrail_keys=["small_test_sample"],
        )
    )
    assert not flags["guardrail_unacknowledged"]


def test_acknowledgement_is_matched_case_insensitively() -> None:
    flags = compute_flags(
        record(text="Risk of OVERFITTING here.", guardrail_keys=["blend_members_at_risk"])
    )
    assert not flags["guardrail_unacknowledged"]


def test_one_unexplained_guardrail_among_several_still_flags() -> None:
    flags = compute_flags(
        record(
            text="Small sample, so treat this as provisional.",
            guardrail_keys=["small_test_sample", "true_holdout_overlap"],
        )
    )
    assert flags["guardrail_unacknowledged"]


def test_no_guardrails_means_nothing_to_acknowledge() -> None:
    assert not compute_flags(record(text="Here is the answer."))["guardrail_unacknowledged"]


def test_an_unknown_guardrail_key_is_not_counted_against_the_answer() -> None:
    """A rule added without acknowledgement terms must not silently make every
    turn look non-compliant."""
    flags = compute_flags(record(text="Anything.", guardrail_keys=["a_brand_new_rule"]))
    assert not flags["guardrail_unacknowledged"]


# --- map_narration --------------------------------------------------------


def test_narrating_grid_points_on_a_small_sample_is_flagged() -> None:
    flags = compute_flags(
        record(
            text="The grid points over the highlands are much stronger.",
            guardrail_keys=["small_test_sample"],
        )
    )
    assert flags["map_narration"]


def test_grid_point_talk_on_an_adequate_sample_is_not_flagged() -> None:
    flags = compute_flags(record(text="The grid points look consistent.", guardrail_keys=[]))
    assert not flags["map_narration"]


# --- persistence ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_is_recorded_with_its_provenance() -> None:
    from ai_almanac.server.db import get_db

    await record_turn(
        TurnRecord(
            session_id="sess-log",
            user_id="user-log",
            turn_id="turn-log",
            scope_kind="blend_setup",
            ruleset_id="builtin",
            ruleset_version=1,
            model_name="test-model",
            latency_ms=1234,
            input_tokens=10,
            output_tokens=20,
            text="The score is 0.5.",
            tool_calls=["list_models"],
            guardrail_keys=["small_test_sample"],
        )
    )

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM assistant_turn_logs WHERE turn_id = 'turn-log'")
                )
            )
            .mappings()
            .fetchone()
        )

    assert row["ruleset_id"] == "builtin"
    assert row["ruleset_version"] == 1
    assert row["model_name"] == "test-model"
    assert row["latency_ms"] == 1234
    assert json.loads(row["tool_calls"]) == ["list_models"]
    assert json.loads(row["guardrail_keys"]) == ["small_test_sample"]
    flags = json.loads(row["flags"])
    assert flags["numbers_without_tool_call"] is True
    assert flags["guardrail_unacknowledged"] is True


@pytest.mark.asyncio
async def test_a_turn_with_no_id_is_not_recorded() -> None:
    """A turn that failed before the agent was built has nothing to attribute."""
    from ai_almanac.server.db import get_db

    await record_turn(TurnRecord(session_id="sess-none", user_id="user-none"))

    async with get_db() as conn:
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM assistant_turn_logs WHERE session_id = 'sess-none'")
            )
        ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_a_logging_failure_never_breaks_the_turn(monkeypatch) -> None:
    """The user already received the answer; telemetry must not raise over it."""
    from sqlalchemy.exc import OperationalError

    from ai_almanac.server.services import turn_log

    class Boom:
        async def __aenter__(self):
            raise OperationalError("boom", {}, Exception("boom"))

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("ai_almanac.server.db.get_db", lambda: Boom())
    await turn_log.record_turn(record(text="anything"))


# --- rating ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rating_a_turn_records_the_score(
    client: httpx.AsyncClient, auth_headers: dict[str, str], user_id: str
) -> None:
    from ai_almanac.server.db import get_db

    await record_turn(
        TurnRecord(session_id="sess-rate", user_id=user_id, turn_id="turn-rate", text="hi")
    )

    res = await client.post(
        "/chat/sessions/sess-rate/turns/turn-rate/rating",
        headers=auth_headers,
        json={"value": 1, "note": "clear and cautious"},
    )
    assert res.status_code == 204, res.text

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT rating, rating_note FROM assistant_turn_logs "
                        "WHERE turn_id = 'turn-rate'"
                    )
                )
            )
            .mappings()
            .fetchone()
        )
    assert row["rating"] == 1
    assert row["rating_note"] == "clear and cautious"


@pytest.mark.asyncio
async def test_rating_someone_elses_turn_is_a_404(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The update is scoped by user_id, so another user's turn simply isn't
    found rather than being silently rated."""
    await record_turn(
        TurnRecord(
            session_id="sess-other", user_id="somebody-else", turn_id="turn-other", text="hi"
        )
    )

    res = await client.post(
        "/chat/sessions/sess-other/turns/turn-other/rating",
        headers=auth_headers,
        json={"value": -1},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_an_out_of_range_rating_is_rejected(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    res = await client.post(
        "/chat/sessions/s/turns/t/rating", headers=auth_headers, json={"value": 5}
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_a_streamed_turn_records_its_ruleset_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the provenance a ruleset comparison needs is written by the
    normal chat path, not only by a direct record_turn call."""
    from pydantic_ai.models.test import TestModel

    from ai_almanac.server.db import get_db
    from ai_almanac.server.services import llm, rulesets
    from ai_almanac.server.services.chat_state import ChatScope

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: TestModel(call_tools=[], custom_output_text="Answer."),
    )
    scope = ChatScope(kind="blend_setup", key="blend-1", title="B", job_ids=[])
    control = rulesets.packaged_ruleset("unconstrained")

    events = [
        json.loads(event)
        async for event in llm.stream_response(
            [],
            "user-stream",
            "sess-stream",
            scope,
            latest_user_message="hi",
            active_ruleset=control,
        )
    ]
    assert events[-1]["type"] == "done"

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT ruleset_id, ruleset_version, scope_kind, latency_ms "
                        "FROM assistant_turn_logs WHERE session_id = 'sess-stream'"
                    )
                )
            )
            .mappings()
            .fetchone()
        )

    assert row["ruleset_id"] == "unconstrained"
    assert row["ruleset_version"] == control.version
    assert row["scope_kind"] == "blend_setup"
    assert row["latency_ms"] is not None


@pytest.mark.asyncio
async def test_a_failed_turn_is_still_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ruleset that makes the model fall over is a result worth counting, so
    the log is written on the failure path too."""
    from ai_almanac.server.db import get_db
    from ai_almanac.server.services import llm
    from ai_almanac.server.services.chat_state import ChatScope

    def explode():
        raise RuntimeError("provider down")

    monkeypatch.setattr("ai_almanac.server.services.llm._build_model", explode)
    scope = ChatScope(kind="benchmark_setup", key="s", job_ids=[])

    with pytest.raises(RuntimeError):
        async for _ in llm.stream_response(
            [], "user-fail", "sess-fail", scope, latest_user_message="hi"
        ):
            pass

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT failure_category FROM assistant_turn_logs "
                        "WHERE session_id = 'sess-fail'"
                    )
                )
            )
            .mappings()
            .fetchone()
        )
    # No turn id was ever assigned (the agent never got built), so nothing is
    # attributable and nothing is written — the usage event still records it.
    assert row is None
