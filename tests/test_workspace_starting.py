"""Starting a workspace afresh, and naming it once there is something to name."""

from __future__ import annotations

import json
from uuid import uuid4

import sqlalchemy as sa

from ai_almanac.server.db import get_db
from tests.helpers.workspaces import conversation_in


async def _blank(client, headers) -> dict:
    response = await client.post("/workspaces/blank", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _say_something(session_id: str) -> None:
    turn = [{"role": "user", "content": "How did the models do?"}]
    async with get_db() as conn:
        await conn.execute(
            sa.text("UPDATE chat_sessions SET transcript = :t WHERE id = :id"),
            {"t": json.dumps(turn), "id": session_id},
        )


async def test_starting_afresh_twice_opens_the_same_untouched_workspace(
    client, as_new_user
) -> None:
    first = await _blank(client, as_new_user)
    await conversation_in(client, as_new_user, first["id"])
    second = await _blank(client, as_new_user)

    listed = (await client.get("/workspaces", headers=as_new_user)).json()

    assert second["id"] == first["id"]
    assert first["untitled"] is True
    assert [ws["id"] for ws in listed] == [first["id"]]


async def test_a_workspace_with_a_conversation_is_not_started_over(client, as_new_user) -> None:
    used = await _blank(client, as_new_user)
    await _say_something(await conversation_in(client, as_new_user, used["id"]))

    fresh = await _blank(client, as_new_user)

    assert fresh["id"] != used["id"]


async def test_a_workspace_given_an_area_is_not_started_over(client, as_new_user) -> None:
    used = await _blank(client, as_new_user)
    area = {"lat_min": 8.0, "lat_max": 12.0, "lon_min": 36.0, "lon_max": 40.0}
    patched = await client.patch(
        f"/workspaces/{used['id']}/settings", headers=as_new_user, json={"study_area": area}
    )
    assert patched.status_code == 200, patched.text

    fresh = await _blank(client, as_new_user)

    assert fresh["id"] != used["id"]


async def test_another_users_untouched_workspace_is_not_handed_out(client, as_new_user) -> None:
    someone_else = {"X-Forwarded-User": f"pytest-{uuid4()}"}
    theirs = await _blank(client, someone_else)

    mine = await _blank(client, as_new_user)

    assert mine["id"] != theirs["id"]


async def test_naming_a_workspace_keeps_its_description_and_ends_its_untitled_state(
    client, as_new_user
) -> None:
    ws = await _blank(client, as_new_user)
    url = f"/workspaces/{ws['id']}"
    await client.patch(url, headers=as_new_user, json={"description": "Kiremt onset skill"})

    renamed = await client.patch(url, headers=as_new_user, json={"name": "  Ethiopia 2026 "})
    fresh = await _blank(client, as_new_user)

    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert (body["name"], body["description"], body["untitled"]) == (
        "Ethiopia 2026",
        "Kiremt onset skill",
        False,
    )
    assert fresh["id"] != ws["id"], "a named workspace is no longer blank"


async def test_a_workspace_cannot_be_named_nothing(client, as_new_user) -> None:
    ws = await _blank(client, as_new_user)

    response = await client.patch(
        f"/workspaces/{ws['id']}", headers=as_new_user, json={"name": "   "}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Workspace name is required"


async def test_someone_elses_workspace_cannot_be_renamed(client, as_new_user) -> None:
    theirs = await _blank(client, {"X-Forwarded-User": f"pytest-{uuid4()}"})

    response = await client.patch(
        f"/workspaces/{theirs['id']}", headers=as_new_user, json={"name": "Mine now"}
    )

    assert response.status_code == 404
