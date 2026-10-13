"""The assistant ruleset admin API.

Two things matter here: an admin can iterate on the wording at runtime, and a
non-admin cannot reach any of it.
"""

from __future__ import annotations

import httpx
import pytest

from ai_almanac.server.services import rulesets
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.llm import _instructions_for_ruleset


@pytest.mark.asyncio
async def test_listing_reports_the_packaged_rulesets_and_which_is_active(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.get("/assistant/rulesets", headers=as_new_user)

    assert res.status_code == 200, res.text
    by_id = {item["id"]: item for item in res.json()}
    assert by_id["builtin"]["is_active"] is True
    assert by_id["unconstrained"]["is_active"] is False


@pytest.mark.asyncio
async def test_preview_renders_the_exact_prompt_the_chat_path_would_use(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()

    res = await client.post(
        "/assistant/rulesets/builtin/preview",
        headers=as_new_user,
        json={"scope_kind": "blend_setup"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    scope = ChatScope(kind="blend_setup", key="preview", job_ids=[])
    assert body["instructions"] == _instructions_for_ruleset(
        rulesets.packaged_ruleset("builtin"), scope
    )
    # An unresolved placeholder must be visible before activating, not at runtime.
    assert "{{" not in body["instructions"]
    assert body["character_count"] == len(body["instructions"])


@pytest.mark.asyncio
async def test_clone_then_activate_changes_what_chat_resolves(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    """The whole point of Phase 2: change the wording and have it take effect
    without a restart."""
    await rulesets.seed_packaged_rulesets()

    clone = await client.post(
        "/assistant/rulesets/builtin/clone",
        headers=as_new_user,
        json={"id": "builtin-terse", "name": "Built-in, terser", "prompt_sections": []},
    )
    assert clone.status_code == 200, clone.text
    assert clone.json()["source"] == "custom"
    assert clone.json()["version"] == rulesets.packaged_ruleset("builtin").version + 1

    detail = clone.json()
    sections = [
        {**section, "body": "Answer in one sentence."}
        if section["key"] == "output_style"
        else section
        for section in detail["prompt_sections"]
    ]
    save = await client.put(
        "/assistant/rulesets/builtin-terse",
        headers=as_new_user,
        json={
            "id": "builtin-terse",
            "name": "Built-in, terser",
            "version": 2,
            "prompt_sections": sections,
        },
    )
    assert save.status_code == 200, save.text

    activate = await client.post("/assistant/rulesets/builtin-terse/activate", headers=as_new_user)
    assert activate.status_code == 200, activate.text

    active = await rulesets.active_ruleset()
    assert active.id == "builtin-terse"
    assert "Answer in one sentence." in rulesets.build_instructions(active, "blend_setup")
    # The required section survived an edit that did not mention it.
    assert "Interpreting results: caveats" in rulesets.build_instructions(active, "blend_setup")


@pytest.mark.asyncio
async def test_the_comparison_control_cannot_be_made_active(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    """The unconstrained arm exists to be compared against, never deployed:
    activating it would strip every user's assistant of its caveats in one
    click."""
    await rulesets.seed_packaged_rulesets()
    before = await rulesets.active_ruleset()

    response = await client.post("/assistant/rulesets/unconstrained/activate", headers=as_new_user)

    assert response.status_code == 409, response.text
    # The refusal rolled back cleanly: the previous ruleset is still active.
    assert (await rulesets.active_ruleset()).id == before.id
    listing = await client.get("/assistant/rulesets", headers=as_new_user)
    summaries = {entry["id"]: entry for entry in listing.json()}
    assert summaries["unconstrained"]["activatable"] is False
    assert summaries["builtin"]["activatable"] is True


@pytest.mark.asyncio
async def test_cloning_onto_an_existing_id_conflicts(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()
    res = await client.post(
        "/assistant/rulesets/builtin/clone",
        headers=as_new_user,
        json={"id": "unconstrained", "name": "Clash", "prompt_sections": []},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_cloning_reclaims_a_deleted_ruleset_id(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    """Delete is an archive that keeps the row, so without reclaiming, a
    deleted id would block that name forever (issue #122). The new clone's
    version must stay above the buried row's: turn-log rollups key on
    (id, version)."""
    await rulesets.seed_packaged_rulesets()

    first = await client.post(
        "/assistant/rulesets/builtin/clone",
        headers=as_new_user,
        json={"id": "test", "name": "Test", "prompt_sections": []},
    )
    assert first.status_code == 200, first.text
    await rulesets.archive_ruleset("test")

    again = await client.post(
        "/assistant/rulesets/unconstrained/clone",
        headers=as_new_user,
        json={"id": "test", "name": "Test", "prompt_sections": []},
    )
    assert again.status_code == 200, again.text
    assert again.json()["version"] > first.json()["version"]

    stored = {row.ruleset.id: row for row in await rulesets.list_rulesets()}
    assert "test" in stored  # resurrected, no longer archived


@pytest.mark.asyncio
async def test_saving_cannot_disable_a_required_section(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    """The failure mode the old wholesale prompt override had: an edit that
    silently drops the statistical cautions."""
    await rulesets.seed_packaged_rulesets()
    res = await client.put(
        "/assistant/rulesets/builtin-copy",
        headers=as_new_user,
        json={
            "id": "builtin-copy",
            "name": "Copy",
            "prompt_sections": [
                {"key": "caveats", "body": "...", "required": True, "enabled": False}
            ],
        },
    )
    assert res.status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/assistant/rulesets/no-such", None),
        (
            "POST",
            "/assistant/rulesets/no-such/clone",
            {"id": "x", "name": "X", "prompt_sections": []},
        ),
        ("POST", "/assistant/rulesets/no-such/activate", None),
        ("POST", "/assistant/rulesets/no-such/preview", {"scope_kind": "blend_setup"}),
        ("POST", "/assistant/rulesets/no-such/comparison-enabled", {"enabled": True}),
        ("POST", "/assistant/rulesets/no-such/admin-enabled", {"enabled": True}),
        ("DELETE", "/assistant/rulesets/no-such", None),
    ],
)
@pytest.mark.asyncio
async def test_an_unknown_ruleset_is_a_404(
    client: httpx.AsyncClient,
    as_new_user: dict[str, str],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    await rulesets.seed_packaged_rulesets()
    res = await client.request(method, path, headers=as_new_user, json=body)
    assert res.status_code == 404, f"{method} {path} returned {res.status_code}"


@pytest.mark.asyncio
async def test_saving_over_a_packaged_id_is_refused_with_a_conflict(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    """Seeding rewrites packaged rows on every startup, so accepting this save
    would look successful and then revert on the next restart."""
    await rulesets.seed_packaged_rulesets()
    detail = (await client.get("/assistant/rulesets/builtin", headers=as_new_user)).json()

    res = await client.put(
        "/assistant/rulesets/builtin",
        headers=as_new_user,
        json={
            "id": "builtin",
            "name": detail["name"],
            "version": detail["version"],
            "prompt_sections": detail["prompt_sections"],
        },
    )

    assert res.status_code == 409


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
        ("GET", "/assistant/rulesets/builtin"),
        ("PUT", "/assistant/rulesets/builtin"),
        ("POST", "/assistant/rulesets/builtin/clone"),
        ("POST", "/assistant/rulesets/builtin/activate"),
        ("POST", "/assistant/rulesets/builtin/preview"),
        ("POST", "/assistant/rulesets/builtin/comparison-enabled"),
        ("POST", "/assistant/rulesets/builtin/admin-enabled"),
        ("DELETE", "/assistant/rulesets/builtin"),
        ("GET", "/assistant/feedback"),
    ],
)
@pytest.mark.parametrize(
    ("headers", "expected"),
    [({"X-Forwarded-User": "rando"}, 403), ({}, 401)],
    ids=["non-admin", "unidentified"],
)
@pytest.mark.asyncio
async def test_every_endpoint_requires_admin_in_shared(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    headers: dict[str, str],
    expected: int,
) -> None:
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")

    res = await client.request(method, path, headers=headers, json={})

    assert res.status_code == expected, f"{method} {path} returned {res.status_code}"


# --- exposure flag and deletion ---------------------------------------------


@pytest.mark.asyncio
async def test_the_exposure_flag_gates_what_users_see(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()

    async def option_ids() -> set[str]:
        res = await client.get("/assistant/ruleset-options", headers=as_new_user)
        return {item["id"] for item in res.json()["rulesets"]}

    # The active built-in is always listed; everything else needs the flag.
    assert await option_ids() == {"builtin"}

    enabled = await client.post(
        "/assistant/rulesets/unconstrained/comparison-enabled",
        headers=as_new_user,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["comparison_enabled"] is True
    assert await option_ids() == {"builtin", "unconstrained"}

    # The admin listing shows the flag; the user listing never shows hidden rows.
    listed = await client.get("/assistant/rulesets", headers=as_new_user)
    flags = {item["id"]: item["comparison_enabled"] for item in listed.json()}
    assert flags["unconstrained"] is True
    assert flags["builtin"] is False

    disabled = await client.post(
        "/assistant/rulesets/unconstrained/comparison-enabled",
        headers=as_new_user,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert await option_ids() == {"builtin"}


@pytest.mark.asyncio
async def test_admin_preview_is_visible_to_admins_only(
    client: httpx.AsyncClient, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.settings import settings

    await rulesets.seed_packaged_rulesets()
    await rulesets.set_comparison_enabled("builtin", True)

    enabled = await client.post(
        "/assistant/rulesets/unconstrained/admin-enabled",
        headers=as_new_user,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["admin_enabled"] is True

    # The admin's picker includes the preview, badged, and it counts toward
    # comparison availability for the admin.
    res = (await client.get("/assistant/ruleset-options", headers=as_new_user)).json()
    options = {item["id"]: item for item in res["rulesets"]}
    assert options["unconstrained"]["admin_only"] is True
    assert options["builtin"]["admin_only"] is False
    assert res["compare_available"] is True

    # A plain user's picker never shows it.
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    user_res = (
        await client.get("/assistant/ruleset-options", headers={"X-Forwarded-User": "rando"})
    ).json()
    assert {item["id"] for item in user_res["rulesets"]} == {"builtin"}
    assert user_res["compare_available"] is False


@pytest.mark.asyncio
async def test_archiving_a_ruleset_withdraws_its_admin_preview(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()
    await rulesets.save_ruleset(rulesets.Ruleset(id="draft-preview", name="Draft"))
    await rulesets.set_admin_enabled("draft-preview", True)

    async def admin_option_ids() -> set[str]:
        res = await client.get("/assistant/ruleset-options", headers=as_new_user)
        return {item["id"] for item in res.json()["rulesets"]}

    assert "draft-preview" in await admin_option_ids()
    await rulesets.archive_ruleset("draft-preview")
    assert "draft-preview" not in await admin_option_ids()


@pytest.mark.asyncio
async def test_deleting_a_ruleset_archives_it_and_its_provenance_survives(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()
    saved = await client.put(
        "/assistant/rulesets/doomed",
        headers=as_new_user,
        json={
            "id": "doomed",
            "name": "Doomed",
            "prompt_sections": [{"key": "one", "body": "Be brief."}],
        },
    )
    assert saved.status_code == 200, saved.text

    deleted = await client.delete("/assistant/rulesets/doomed", headers=as_new_user)
    assert deleted.status_code == 204

    listed = await client.get("/assistant/rulesets", headers=as_new_user)
    assert "doomed" not in {item["id"] for item in listed.json()}
    # The row survives for turn-log provenance; only the listings lose it.
    assert (await rulesets.get_ruleset("doomed")) is not None

    again = await client.delete("/assistant/rulesets/doomed", headers=as_new_user)
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_packaged_and_active_rulesets_cannot_be_deleted(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    await rulesets.seed_packaged_rulesets()

    packaged = await client.delete("/assistant/rulesets/unconstrained", headers=as_new_user)
    assert packaged.status_code == 409

    saved = await client.put(
        "/assistant/rulesets/active-custom",
        headers=as_new_user,
        json={
            "id": "active-custom",
            "name": "Active custom",
            "prompt_sections": [{"key": "one", "body": "Be brief."}],
        },
    )
    assert saved.status_code == 200, saved.text
    await client.post("/assistant/rulesets/active-custom/activate", headers=as_new_user)
    active = await client.delete("/assistant/rulesets/active-custom", headers=as_new_user)
    assert active.status_code == 409
