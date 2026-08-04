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

    for method, path, payload in (
        ("POST", "/assistant/compare", {"message": "hi", "variants": []}),
        ("POST", "/assistant/comparisons/abc/vote", {}),
        ("DELETE", "/assistant/comparisons/abc", None),
    ):
        res = await client.request(method, path, headers=headers, json=payload)
        assert res.status_code == 403, f"{method} {path} returned {res.status_code}"


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
