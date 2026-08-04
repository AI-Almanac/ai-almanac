"""Side-by-side ruleset comparison.

Four properties matter, in this order:

1. nothing can be launched from the playground — the submit tools are withheld
   from every variant, at registration;
2. the two arms run against independent scratch sessions, so neither can
   overwrite the other's proposed configuration;
3. those scratch sessions never appear as conversations the user started;
4. a vote lands on both arms' turn logs, which is the data the whole phase
   exists to collect.
"""

from __future__ import annotations

import json

import httpx
import pytest
import sqlalchemy as sa
from pydantic_ai.models.test import TestModel

from ai_almanac.server.services import assistant_compare, rulesets


def _sse_events(body: str) -> list[dict]:
    return [
        json.loads(chunk.removeprefix("data: "))
        for chunk in body.strip().split("\n\n")
        if chunk.startswith("data: ")
    ]


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: TestModel(call_tools=[], custom_output_text="Comparison answer."),
    )


@pytest.mark.asyncio
async def test_every_variant_loses_the_submit_tools() -> None:
    """The playground compares what the assistant proposes, never what it runs."""
    await rulesets.seed_packaged_rulesets()

    resolved = await assistant_compare.variant_ruleset(
        assistant_compare.VariantSpec(ruleset_id="builtin")
    )

    assert set(assistant_compare.COMPARE_DENIED_TOOLS) <= set(resolved.tool_policy.deny)


@pytest.mark.asyncio
async def test_denied_submit_tools_are_absent_from_the_agent_schema() -> None:
    """Denial is at registration, so there is no schema entry to attempt."""
    from ai_almanac.server.services import llm
    from ai_almanac.server.services.chat_state import ChatScope

    await rulesets.seed_packaged_rulesets()
    resolved = await assistant_compare.variant_ruleset(
        assistant_compare.VariantSpec(ruleset_id="builtin")
    )

    agent = llm._build_agent(
        ChatScope(kind="blend_setup", key="k", job_ids=[]), resolved, TestModel()
    )
    tool_names = {name for toolset in agent.toolsets for name in getattr(toolset, "tools", {})}

    assert "submit_benchmark" not in tool_names
    assert "submit_blend" not in tool_names
    # The comparison is still worth running: the config-building tools remain.
    assert "update_blend_config" in tool_names


@pytest.mark.asyncio
async def test_a_variant_may_pin_a_different_model_on_the_same_ruleset() -> None:
    await rulesets.seed_packaged_rulesets()

    resolved = await assistant_compare.variant_ruleset(
        assistant_compare.VariantSpec(ruleset_id="builtin", model="openai:gpt-4o-mini")
    )

    assert resolved.model == "openai:gpt-4o-mini"
    assert resolved.id == "builtin"


@pytest.mark.asyncio
async def test_an_unknown_ruleset_is_rejected_before_anything_streams(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    res = await client.post(
        "/assistant/compare",
        headers=auth_headers,
        json={
            "message": "which model is best?",
            "variants": [{"ruleset_id": "builtin"}, {"ruleset_id": "no-such-ruleset"}],
        },
    )

    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_compare_streams_both_variants_and_hides_the_scratch_sessions(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare",
        headers=auth_headers,
        json={
            "message": "which model is best?",
            "variants": [{"ruleset_id": "builtin"}, {"ruleset_id": "unconstrained"}],
        },
    )

    assert res.status_code == 200, res.text
    events = _sse_events(res.text)
    started = events[0]
    assert started["type"] == "comparison_started"
    assert [variant["ruleset_id"] for variant in started["variants"]] == [
        "builtin",
        "unconstrained",
    ]
    session_ids = [variant["session_id"] for variant in started["variants"]]
    assert len(set(session_ids)) == 2, "each arm needs its own session to mutate"
    assert events[-1]["type"] == "comparison_complete"

    # Every arm produced its own answer, tagged with its column.
    done = {event["variant"]: event for event in events if event.get("type") == "done"}
    assert set(done) == {0, 1}
    assert done[0]["turn"]["content"] == "Comparison answer."

    listed = await client.get("/chat/sessions", headers=auth_headers)
    assert listed.status_code == 200
    assert not (set(session_ids) & {item["id"] for item in listed.json()})


@pytest.mark.asyncio
async def test_a_vote_scores_both_arms_and_a_tie_scores_neither(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    _test_engine,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare",
        headers=auth_headers,
        json={
            "message": "which model is best?",
            "variants": [{"ruleset_id": "builtin"}, {"ruleset_id": "unconstrained"}],
        },
    )
    started = _sse_events(res.text)[0]
    comparison_id = started["comparison_id"]
    winner, loser = (variant["session_id"] for variant in started["variants"])

    vote = await client.post(
        f"/assistant/comparisons/{comparison_id}/vote",
        headers=auth_headers,
        json={"winner_session_id": winner, "note": "clearer about the sample size"},
    )
    assert vote.status_code == 200, vote.text
    assert vote.json()["rated_turns"] == 2

    async def ratings() -> dict[str, int]:
        async with _test_engine.begin() as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT session_id, rating FROM assistant_turn_logs "
                        "WHERE comparison_id = :cid"
                    ),
                    {"cid": comparison_id},
                )
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    assert await ratings() == {winner: 1, loser: -1}

    tie = await client.post(
        f"/assistant/comparisons/{comparison_id}/vote",
        headers=auth_headers,
        json={"winner_session_id": None},
    )
    assert tie.status_code == 200
    assert await ratings() == {winner: 0, loser: 0}


@pytest.mark.asyncio
async def test_a_vote_cannot_name_a_session_outside_the_comparison(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare",
        headers=auth_headers,
        json={
            "message": "which model is best?",
            "variants": [{"ruleset_id": "builtin"}, {"ruleset_id": "unconstrained"}],
        },
    )
    comparison_id = _sse_events(res.text)[0]["comparison_id"]

    vote = await client.post(
        f"/assistant/comparisons/{comparison_id}/vote",
        headers=auth_headers,
        json={"winner_session_id": "some-other-session"},
    )

    assert vote.status_code == 400, vote.text


@pytest.mark.asyncio
async def test_discarding_a_comparison_removes_its_scratch_sessions(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare",
        headers=auth_headers,
        json={
            "message": "which model is best?",
            "variants": [{"ruleset_id": "builtin"}, {"ruleset_id": "unconstrained"}],
        },
    )
    started = _sse_events(res.text)[0]
    comparison_id = started["comparison_id"]

    deleted = await client.delete(f"/assistant/comparisons/{comparison_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert await assistant_compare.comparison_session_ids(comparison_id, "unused") == []

    again = await client.delete(f"/assistant/comparisons/{comparison_id}", headers=auth_headers)
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_cloning_a_session_carries_its_configuration_into_both_arms(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
) -> None:
    """Comparing on real configuration state is why the arms are clones."""
    await rulesets.seed_packaged_rulesets()

    created = await client.post(
        "/chat/sessions",
        headers=auth_headers,
        json={
            "scope": {"kind": "blend_setup", "key": "blend-1", "title": "B", "job_ids": []},
            "title": "Source",
        },
    )
    source_id = created.json()["id"]

    res = await client.post(
        "/assistant/compare",
        headers=auth_headers,
        json={
            "message": "add a third model",
            "source_session_id": source_id,
            "variants": [{"ruleset_id": "builtin"}, {"ruleset_id": "unconstrained"}],
        },
    )
    assert res.status_code == 200, res.text
    started = _sse_events(res.text)[0]

    for variant in started["variants"]:
        detail = await client.get(f"/chat/sessions/{variant['session_id']}", headers=auth_headers)
        assert detail.status_code == 200
        assert detail.json()["scope"]["key"] == "blend-1"

    # The source conversation is untouched by either arm.
    source = await client.get(f"/chat/sessions/{source_id}", headers=auth_headers)
    assert source.json()["transcript"] == []


@pytest.mark.asyncio
async def test_comparison_endpoints_require_admin_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    headers = {"X-Forwarded-User": "rando"}

    # Naming arbitrary variants stays an admin capability.
    res = await client.post(
        "/assistant/compare", headers=headers, json={"message": "hi", "variants": []}
    )
    assert res.status_code == 403, res.text

    # Voting, discarding and the blind flow are open to any identified user —
    # they are scoped to the caller's own comparisons.
    for method, path, payload in (
        ("POST", "/assistant/comparisons/abc/vote", {}),
        ("DELETE", "/assistant/comparisons/abc", None),
        ("POST", "/assistant/compare/blind", {"message": "hi"}),
        ("GET", "/assistant/ruleset-options", None),
    ):
        res = await client.request(method, path, headers=headers, json=payload)
        assert res.status_code != 403, f"{method} {path} returned 403 for a plain user"
        unauthenticated = await client.request(method, path, json=payload)
        assert unauthenticated.status_code == 401, f"{method} {path} open when unidentified"


@pytest.mark.asyncio
async def test_a_scratch_session_cannot_submit_a_run(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
) -> None:
    """Withholding the submit tools stops the model, not the session id.

    The comparison's session ids are visible to whoever ran it, and the submit
    endpoints launch a session's configuration without involving the model at all.
    So the refusal has to live on the session, not only in the tool policy.
    """
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare",
        headers=auth_headers,
        json={
            "message": "which model is best?",
            "variants": [{"ruleset_id": "builtin"}, {"ruleset_id": "unconstrained"}],
        },
    )
    session_id = _sse_events(res.text)[0]["variants"][0]["session_id"]

    for path in (
        f"/chat/sessions/{session_id}/benchmark/submit",
        f"/chat/sessions/{session_id}/blend/submit",
    ):
        submitted = await client.post(path, headers=auth_headers, json={})
        assert submitted.status_code == 400, submitted.text
        assert "comparison" in submitted.json()["detail"]


# --- blind user-facing comparisons -----------------------------------------
#
# Regular users compare active-vs-candidate without knowing which is which:
# the server picks and shuffles the arms, the stream names them only by index,
# and identities come out in the vote response.


@pytest.fixture
def candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "assistant_comparison_candidate", "unconstrained")


@pytest.mark.asyncio
async def test_a_blind_comparison_never_names_its_arms(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare/blind",
        headers=auth_headers,
        json={"message": "which model is best?"},
    )

    assert res.status_code == 200, res.text
    started = _sse_events(res.text)[0]
    assert started["type"] == "comparison_started"
    for variant in started["variants"]:
        assert set(variant) == {"variant", "session_id"}
    # Nothing anywhere in the stream identifies a ruleset.
    for leak in ("ruleset_id", "ruleset_name", "builtin", "unconstrained", "Unconstrained"):
        assert leak not in res.text, f"{leak!r} leaked into the blind stream"

    # The scratch sessions are fetchable by their owner, so their titles must
    # not unblind the arms either.
    for variant in started["variants"]:
        detail = await client.get(f"/chat/sessions/{variant['session_id']}", headers=auth_headers)
        title = detail.json()["title"]
        assert "Arm" in title
        assert "Built-in" not in title and "Unconstrained" not in title


@pytest.mark.asyncio
async def test_the_arm_order_is_shuffled(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint really applies the shuffle: force it to reverse and the
    candidate lands in arm 0."""
    await rulesets.seed_packaged_rulesets()
    monkeypatch.setattr("ai_almanac.server.routers.assistant.random.shuffle", list.reverse)

    res = await client.post(
        "/assistant/compare/blind", headers=auth_headers, json={"message": "hi"}
    )
    started = _sse_events(res.text)[0]

    vote = await client.post(
        f"/assistant/comparisons/{started['comparison_id']}/vote",
        headers=auth_headers,
        json={"winner_session_id": None},
    )
    by_session = {arm["session_id"]: arm["ruleset_id"] for arm in vote.json()["arms"]}
    ordered = [by_session[variant["session_id"]] for variant in started["variants"]]

    assert ordered == ["unconstrained", "builtin"]


@pytest.mark.asyncio
async def test_the_vote_response_reveals_the_arms(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare/blind", headers=auth_headers, json={"message": "hi"}
    )
    started = _sse_events(res.text)[0]
    winner = started["variants"][0]["session_id"]

    vote = await client.post(
        f"/assistant/comparisons/{started['comparison_id']}/vote",
        headers=auth_headers,
        json={"winner_session_id": winner},
    )

    assert vote.status_code == 200, vote.text
    arms = vote.json()["arms"]
    assert {arm["ruleset_id"] for arm in arms} == {"builtin", "unconstrained"}
    assert all(arm["ruleset_name"] for arm in arms)


@pytest.mark.asyncio
async def test_blind_comparisons_are_offered_only_with_a_usable_candidate(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.settings import settings

    await rulesets.seed_packaged_rulesets()

    async def availability() -> bool:
        res = await client.get("/assistant/ruleset-options", headers=auth_headers)
        return res.json()["compare_available"]

    async def compare_status() -> int:
        res = await client.post(
            "/assistant/compare/blind", headers=auth_headers, json={"message": "hi"}
        )
        return res.status_code

    monkeypatch.setattr(settings, "assistant_comparison_candidate", "")
    assert not await availability()
    assert await compare_status() == 409

    monkeypatch.setattr(settings, "assistant_comparison_candidate", "builtin")  # == active
    assert not await availability()
    assert await compare_status() == 409

    monkeypatch.setattr(settings, "assistant_comparison_candidate", "no-such-ruleset")
    assert not await availability()
    assert await compare_status() == 409


@pytest.mark.asyncio
async def test_ruleset_options_expose_no_prompt_or_policy(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.get("/assistant/ruleset-options", headers=auth_headers)

    assert res.status_code == 200
    listed = res.json()["rulesets"]
    assert {item["id"] for item in listed} >= {"builtin", "unconstrained"}
    for item in listed:
        assert set(item) == {"id", "name", "description", "is_active"}


@pytest.mark.asyncio
async def test_a_blind_scratch_session_cannot_submit_a_run(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare/blind", headers=auth_headers, json={"message": "hi"}
    )
    session_id = _sse_events(res.text)[0]["variants"][0]["session_id"]

    for path in (
        f"/chat/sessions/{session_id}/benchmark/submit",
        f"/chat/sessions/{session_id}/blend/submit",
    ):
        submitted = await client.post(path, headers=auth_headers, json={})
        assert submitted.status_code == 400, submitted.text


@pytest.mark.asyncio
async def test_a_vote_is_scoped_to_the_comparisons_owner(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/compare/blind", headers=auth_headers, json={"message": "hi"}
    )
    comparison_id = _sse_events(res.text)[0]["comparison_id"]

    with pytest.raises(assistant_compare.UnknownSessionError):
        await assistant_compare.record_vote(comparison_id, "someone-else", None)
    assert await assistant_compare.comparison_arms(comparison_id, "someone-else") == []


# --- multi-turn comparisons --------------------------------------------------
#
# The side-by-side view is a dialogue: follow-up messages continue both arms'
# scratch conversations under their original rulesets, and one vote covers
# every turn of the comparison.


@pytest.mark.asyncio
async def test_a_follow_up_message_continues_both_arms(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
    _test_engine,
) -> None:
    await rulesets.seed_packaged_rulesets()

    first = await client.post(
        "/assistant/compare/blind", headers=auth_headers, json={"message": "which model is best?"}
    )
    started = _sse_events(first.text)[0]
    comparison_id = started["comparison_id"]
    session_ids = {variant["session_id"] for variant in started["variants"]}

    followup = await client.post(
        f"/assistant/comparisons/{comparison_id}/message",
        headers=auth_headers,
        json={"message": "and for a shorter lead time?"},
    )

    assert followup.status_code == 200, followup.text
    events = _sse_events(followup.text)
    restarted = events[0]
    assert restarted["type"] == "comparison_started"
    # Same scratch sessions — the conversation continues rather than restarting —
    # and still no ruleset identity anywhere in the stream.
    assert {variant["session_id"] for variant in restarted["variants"]} == session_ids
    for leak in ("ruleset_id", "ruleset_name", "builtin", "unconstrained"):
        assert leak not in followup.text
    done = {event["variant"]: event for event in events if event.get("type") == "done"}
    assert set(done) == {0, 1}

    # Two rounds logged two turns per arm; one vote rates all four.
    async with _test_engine.begin() as conn:
        turns = (
            await conn.execute(
                sa.text("SELECT COUNT(*) FROM assistant_turn_logs WHERE comparison_id = :cid"),
                {"cid": comparison_id},
            )
        ).scalar_one()
    assert turns == 4

    vote = await client.post(
        f"/assistant/comparisons/{comparison_id}/vote",
        headers=auth_headers,
        json={"winner_session_id": sorted(session_ids)[0]},
    )
    assert vote.json()["rated_turns"] == 4


@pytest.mark.asyncio
async def test_a_follow_up_arm_still_lacks_the_submit_tools(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
    user_id: str,
) -> None:
    await rulesets.seed_packaged_rulesets()

    first = await client.post(
        "/assistant/compare/blind", headers=auth_headers, json={"message": "hi"}
    )
    comparison_id = _sse_events(first.text)[0]["comparison_id"]

    resumed = await assistant_compare.resume_comparison(comparison_id, user_id)
    for variant in resumed.variants:
        assert set(assistant_compare.COMPARE_DENIED_TOOLS) <= set(variant.ruleset.tool_policy.deny)


@pytest.mark.asyncio
async def test_a_comparison_cannot_be_continued_by_someone_else(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    stub_model: None,
    candidate: None,
) -> None:
    await rulesets.seed_packaged_rulesets()

    first = await client.post(
        "/assistant/compare/blind", headers=auth_headers, json={"message": "hi"}
    )
    comparison_id = _sse_events(first.text)[0]["comparison_id"]

    with pytest.raises(assistant_compare.UnknownSessionError):
        await assistant_compare.resume_comparison(comparison_id, "someone-else")

    missing = await client.post(
        "/assistant/comparisons/no-such-comparison/message",
        headers=auth_headers,
        json={"message": "hi"},
    )
    assert missing.status_code == 404
