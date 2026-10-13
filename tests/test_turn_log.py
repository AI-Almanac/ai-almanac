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


# --- names_internal_terms ------------------------------------------------

OFFERED = ["get_job_metrics", "get_spatial_summary", "rerun_job"]


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("Use `get_job_metrics` / `get_spatial_summary` once the jobs finish.", True),
        ("I can call rerun_job with parallel off.", True),
        ("Set per_model_params → parallel: false for FuXi.", True),
        (
            "Once the runs finish I can map mean absolute error and rerun FuXi one year at a time.",
            False,
        ),
    ],
)
def test_tool_names_and_config_paths_shown_to_the_user_are_flagged(
    text: str, flagged: bool
) -> None:
    flags = compute_flags(record(text=text, offered_tools=OFFERED))
    assert flags["names_internal_terms"] is flagged


# --- numbers_without_tool_call -------------------------------------------


@pytest.mark.parametrize(
    ("text", "tool_calls", "flagged"),
    [
        pytest.param("The blend scores 0.42 RPSS.", [], True, id="decimal-without-data"),
        pytest.param(
            "The blend scores 0.42 RPSS.", ["get_blend_results"], False, id="after-reading-data"
        ),
        # list_* returns names, not measurements, so quoting a score after only
        # listing models is exactly the case worth counting.
        pytest.param("AIFS reaches 0.31.", ["list_models"], True, id="listing-is-not-reading"),
        # The domain rules are written in integers and conceptual answers come
        # from knowledge; counting integers would flag every one of those.
        pytest.param(
            "Under 10 test years results are noisy, and MAE under 5 days is strong skill.",
            [],
            False,
            id="integers-in-conceptual-answers",
        ),
        pytest.param("The median is 3.5 days.", ["run_code"], False, id="running-code-reads-data"),
    ],
)
def test_numbers_quoted_without_reading_data_are_flagged(
    text: str, tool_calls: list[str], flagged: bool
) -> None:
    flags = compute_flags(record(text=text, tool_calls=tool_calls))
    assert flags["numbers_without_tool_call"] is flagged


# --- guardrail_unacknowledged --------------------------------------------


@pytest.mark.parametrize(
    ("text", "guardrail_keys", "flagged"),
    [
        pytest.param(
            "AIFS is the best model here.", ["small_test_sample"], True, id="ignored-guardrail"
        ),
        pytest.param(
            "AIFS leads, but this is a small sample so the difference may not be real.",
            ["small_test_sample"],
            False,
            id="explained-guardrail",
        ),
        pytest.param(
            "Risk of OVERFITTING here.",
            ["blend_members_at_risk"],
            False,
            id="case-insensitive-acknowledgement",
        ),
        pytest.param(
            "Small sample, so treat this as provisional.",
            ["small_test_sample", "true_holdout_overlap"],
            True,
            id="one-of-several-unexplained",
        ),
        pytest.param("Here is the answer.", [], False, id="no-guardrails"),
        # A rule added without acknowledgement terms must not silently make
        # every turn look non-compliant.
        pytest.param("Anything.", ["a_brand_new_rule"], False, id="unknown-guardrail-key"),
    ],
)
def test_guardrails_the_answer_does_not_acknowledge_are_flagged(
    text: str, guardrail_keys: list[str], flagged: bool
) -> None:
    flags = compute_flags(record(text=text, guardrail_keys=guardrail_keys))
    assert flags["guardrail_unacknowledged"] is flagged


# --- map_narration --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "guardrail_keys", "flagged"),
    [
        ("The grid points over the highlands are much stronger.", ["small_test_sample"], True),
        ("The grid points look consistent.", [], False),
    ],
)
def test_narrating_grid_points_is_flagged_only_on_a_small_sample(
    text: str, guardrail_keys: list[str], flagged: bool
) -> None:
    flags = compute_flags(record(text=text, guardrail_keys=guardrail_keys))
    assert flags["map_narration"] is flagged


# --- persistence ----------------------------------------------------------


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
    client: httpx.AsyncClient, as_new_user: dict[str, str], user_id: str
) -> None:
    from ai_almanac.server.db import get_db

    await record_turn(
        TurnRecord(session_id="sess-rate", user_id=user_id, turn_id="turn-rate", text="hi")
    )

    res = await client.post(
        "/chat/sessions/sess-rate/turns/turn-rate/rating",
        headers=as_new_user,
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
    client: httpx.AsyncClient, as_new_user: dict[str, str]
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
        headers=as_new_user,
        json={"value": -1},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_an_out_of_range_rating_is_rejected(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    res = await client.post(
        "/chat/sessions/s/turns/t/rating", headers=as_new_user, json={"value": 5}
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
    from ai_almanac.settings import settings

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
                        "SELECT ruleset_id, ruleset_version, scope_kind, model_name, latency_ms "
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
    assert row["model_name"] == settings.llm_model
    assert row["latency_ms"] is not None


@pytest.mark.asyncio
async def test_a_turn_that_fails_mid_stream_is_recorded_with_its_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ruleset that makes the model fall over is a result worth counting, so
    the log is written on the failure path too."""
    from pydantic_ai.models.function import FunctionModel

    from ai_almanac.server.db import get_db
    from ai_almanac.server.services import llm
    from ai_almanac.server.services.chat_state import ChatScope

    async def provider_drops_the_stream(messages, info):
        raise RuntimeError("provider down")
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: FunctionModel(stream_function=provider_drops_the_stream),
    )
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
                        "SELECT turn_id, ruleset_id, failure_category FROM assistant_turn_logs "
                        "WHERE session_id = 'sess-fail'"
                    )
                )
            )
            .mappings()
            .fetchone()
        )
    assert row is not None
    assert row["turn_id"]
    assert row["ruleset_id"]
    assert row["failure_category"]


@pytest.mark.asyncio
async def test_a_turn_that_fails_before_the_agent_is_built_is_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No turn id was ever assigned, so there is nothing to attribute."""
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
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM assistant_turn_logs WHERE session_id = 'sess-fail'")
            )
        ).scalar_one()
    assert count == 0


# --- feedback summary -------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_summary_rolls_up_votes_ratings_and_flags() -> None:
    from ai_almanac.server.db import get_db
    from ai_almanac.server.services.turn_log import feedback_summary, rate_turn

    def fb_record(turn_id: str, **kwargs) -> TurnRecord:
        return TurnRecord(
            session_id=f"sess-{turn_id}",
            user_id="user-fb",
            turn_id=turn_id,
            ruleset_id="fb-test",
            ruleset_version=7,
            **kwargs,
        )

    # A thumbs-up, a comparison tie, and an unrated turn that trips a flag.
    await record_turn(fb_record("fb-up", text="fine", tool_calls=["get_job_metrics"]))
    await rate_turn("sess-fb-up", "fb-up", "user-fb", 1, None)
    await record_turn(fb_record("fb-tie", comparison_id="cmp-fb", text="fine"))
    async with get_db() as conn:
        await conn.execute(
            text("UPDATE assistant_turn_logs SET rating = 0 WHERE turn_id = 'fb-tie'")
        )
    await record_turn(fb_record("fb-flagged", text="The score is 0.53."))

    summary = await feedback_summary()
    group = next(g for g in summary if g["ruleset_id"] == "fb-test")

    assert group["ruleset_version"] == 7
    assert group["turns"] == 3
    assert group["rated"] == 2
    assert group["wins"] == 1
    assert group["losses"] == 0
    assert group["ties"] == 1
    assert group["flag_counts"]["numbers_without_tool_call"] == 1
