"""The assistant ruleset admin API.

Two things matter here: an admin can iterate on the wording at runtime, and a
non-admin cannot reach any of it. The guardrail thresholds are exposed read-only
— they are a platform setting, because the submission chokepoint reads the same
value.
"""

from __future__ import annotations

import httpx
import pytest

from ai_almanac.server.services import rulesets


@pytest.mark.asyncio
async def test_listing_reports_the_packaged_rulesets_and_which_is_active(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.get("/assistant/rulesets", headers=auth_headers)

    assert res.status_code == 200, res.text
    by_id = {item["id"]: item for item in res.json()}
    assert by_id["builtin"]["is_active"] is True
    assert by_id["unconstrained"]["is_active"] is False
    assert "caveats" in by_id["builtin"]["section_keys"]


@pytest.mark.asyncio
async def test_preview_renders_the_exact_prompt_the_chat_path_would_use(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/rulesets/builtin/preview",
        headers=auth_headers,
        json={"scope_kind": "blend_setup"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert "Interpreting results: caveats" in body["instructions"]
    assert "risk of overfitting" in body["instructions"]
    # An unresolved placeholder must be visible before activating, not at runtime.
    assert "{{" not in body["instructions"]
    assert body["character_count"] == len(body["instructions"])


@pytest.mark.asyncio
async def test_preview_rejects_an_unknown_scope_kind(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    res = await client.post(
        "/assistant/rulesets/builtin/preview",
        headers=auth_headers,
        json={"scope_kind": "not_a_scope"},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_clone_then_activate_changes_what_chat_resolves(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The whole point of Phase 2: change the wording and have it take effect
    without a restart."""
    await rulesets.seed_packaged_rulesets()

    clone = await client.post(
        "/assistant/rulesets/builtin/clone",
        headers=auth_headers,
        json={"id": "builtin-terse", "name": "Built-in, terser", "prompt_sections": []},
    )
    assert clone.status_code == 200, clone.text
    assert clone.json()["source"] == "custom"
    assert clone.json()["version"] == 2

    detail = clone.json()
    sections = [
        {**section, "body": "Answer in one sentence."}
        if section["key"] == "output_style"
        else section
        for section in detail["prompt_sections"]
    ]
    save = await client.put(
        "/assistant/rulesets/builtin-terse",
        headers=auth_headers,
        json={
            "id": "builtin-terse",
            "name": "Built-in, terser",
            "version": 2,
            "prompt_sections": sections,
        },
    )
    assert save.status_code == 200, save.text

    activate = await client.post("/assistant/rulesets/builtin-terse/activate", headers=auth_headers)
    assert activate.status_code == 200, activate.text

    active = await rulesets.active_ruleset()
    assert active.id == "builtin-terse"
    assert "Answer in one sentence." in rulesets.build_instructions(active, "blend_setup")
    # The required section survived an edit that did not mention it.
    assert "Interpreting results: caveats" in rulesets.build_instructions(active, "blend_setup")

    await rulesets.activate_ruleset("builtin")


@pytest.mark.asyncio
async def test_cloning_onto_an_existing_id_conflicts(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()
    res = await client.post(
        "/assistant/rulesets/builtin/clone",
        headers=auth_headers,
        json={"id": "unconstrained", "name": "Clash", "prompt_sections": []},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_saving_cannot_disable_a_required_section(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The failure mode the old wholesale prompt override had: an edit that
    silently drops the statistical cautions."""
    await rulesets.seed_packaged_rulesets()
    res = await client.put(
        "/assistant/rulesets/builtin-copy",
        headers=auth_headers,
        json={
            "id": "builtin-copy",
            "name": "Copy",
            "prompt_sections": [
                {"key": "caveats", "body": "...", "required": True, "enabled": False}
            ],
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_guardrail_thresholds_are_reported_read_only(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Shown so an admin can see what the {{placeholders}} resolve to. There is
    no PUT: changing them is a platform setting, because the chokepoint reads the
    same value."""
    res = await client.get("/assistant/guardrails", headers=auth_headers)
    assert res.status_code == 200, res.text
    assert res.json()["min_training_years"] >= 1

    assert (
        await client.put("/assistant/guardrails", headers=auth_headers, json={})
    ).status_code == 405


@pytest.mark.asyncio
async def test_an_unknown_ruleset_is_a_404(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    res = await client.get("/assistant/rulesets/no-such-thing", headers=auth_headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_saving_over_a_packaged_id_is_refused_with_a_conflict(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Seeding rewrites packaged rows on every startup, so accepting this save
    would look successful and then revert on the next restart."""
    await rulesets.seed_packaged_rulesets()
    detail = (await client.get("/assistant/rulesets/builtin", headers=auth_headers)).json()

    res = await client.put(
        "/assistant/rulesets/builtin",
        headers=auth_headers,
        json={
            "id": "builtin",
            "name": detail["name"],
            "version": detail["version"],
            "prompt_sections": detail["prompt_sections"],
        },
    )

    assert res.status_code == 409
    assert "Clone it" in res.json()["detail"]


# --- admin gating ---------------------------------------------------------
#
# The suite runs without auth, so every request in the tests above is admin by
# default and none of them exercise the gate. These do, using the shared-mode
# pattern from test_auth.py. A ruleset decides what the assistant says to every
# user of the deployment, so a non-admin must not be able to read one, let alone
# activate one.


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/assistant/rulesets"),
        ("GET", "/assistant/guardrails"),
        ("GET", "/assistant/rulesets/builtin"),
        ("PUT", "/assistant/rulesets/builtin"),
        ("POST", "/assistant/rulesets/builtin/clone"),
        ("POST", "/assistant/rulesets/builtin/activate"),
        ("POST", "/assistant/rulesets/builtin/preview"),
    ],
)
@pytest.mark.asyncio
async def test_every_endpoint_requires_admin_in_shared(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")

    res = await client.request(method, path, headers={"X-Forwarded-User": "rando"}, json={})

    assert res.status_code == 403, f"{method} {path} returned {res.status_code}"


@pytest.mark.asyncio
async def test_endpoints_reject_an_unidentified_caller_in_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "auth_mode", "proxy")
    assert (await client.get("/assistant/rulesets")).status_code == 401
