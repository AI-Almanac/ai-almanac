"""What a workspace calls its results: file names, analysis names, and supporting files."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from ai_almanac.server.services import analysis_names, ledger, workspaces
from ai_almanac.server.services.turn_log import TurnRecord
from tests.helpers.files import write_nc, write_png
from tests.helpers.sse import sse_events
from tests.helpers.workspaces import workspace_with_session


async def _made_by_turn(
    user_id: str, wid: str, session_id: str, turn_id: str, files: list[ledger.OutputFile]
) -> list[dict]:
    turn = TurnRecord(session_id=session_id, user_id=user_id, scope_kind="workspace")
    turn.turn_id = turn_id
    _, artifacts = await ledger.write_step(
        ledger.StepDraft(primitive="figure.test", version="1.0.0", params={}),
        ledger.Attribution.by_assistant(user_id, session_id, approved=False, turn=turn),
        files,
    )
    await workspaces.add_artifacts(
        wid,
        [
            (a, workspaces.NewItem(f.label, f.title, f.supporting))
            for a, f in zip(artifacts, files, strict=True)
        ],
    )
    return artifacts


@pytest.mark.asyncio
async def test_a_file_keeps_the_name_it_was_given_until_the_user_renames_it(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    [figure] = await _made_by_turn(
        user_id,
        wid,
        session["id"],
        "turn-1",
        [
            ledger.OutputFile(
                path=write_png(tmp_path / "x_p_within_14d.png"),
                label="x_p_within_14d",
                title="Onset within 14 days, by state",
            )
        ],
    )
    base = f"/workspaces/{wid}/artifacts/{figure['id']}"

    [named] = await workspaces.listing(wid)
    renamed = await client.patch(base, headers=as_new_user, json={"title": "  Kiremt   onset map "})
    empty = await client.patch(base, headers=as_new_user, json={"title": "   "})
    restored = await client.patch(base, headers=as_new_user, json={"title": None})
    missing = await client.patch(
        f"/workspaces/{wid}/artifacts/nope", headers=as_new_user, json={"title": "x"}
    )

    assert named["title"] == "Onset within 14 days, by state"
    assert renamed.json()["title"] == "Kiremt onset map"
    assert renamed.json()["label"] == "x_p_within_14d", "the assistant's handle never changes"
    assert empty.status_code == 400
    assert restored.json()["title"] == "X p within 14d"
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_files_a_result_was_made_from_are_marked_as_supporting(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await _made_by_turn(
        user_id,
        wid,
        session["id"],
        "turn-1",
        [
            ledger.OutputFile(path=write_png(tmp_path / "map.png"), label="map"),
            ledger.OutputFile(
                path=write_nc(tmp_path / "outlines.nc"), label="outlines", supporting=True
            ),
        ],
    )

    detail = (await client.get(f"/workspaces/{wid}", headers=as_new_user)).json()

    assert {a["label"]: a["supporting"] for a in detail["artifacts"]} == {
        "map": False,
        "outlines": True,
    }
    assert await workspaces.turn_file_titles(wid, "turn-1") == ["Map"]


@pytest.mark.asyncio
async def test_the_assistant_names_a_turns_results_but_never_over_the_users_name(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await _made_by_turn(
        user_id,
        wid,
        session["id"],
        "turn-1",
        [ledger.OutputFile(path=write_png(tmp_path / "map.png"), label="map")],
    )
    model = TestModel(custom_output_text='"Amhara zones: FuXi onset, July 2024."')

    named = await analysis_names.name_turn(wid, "turn-1", "Which zones see onset?", model)
    renamed = await client.patch(
        f"/workspaces/{wid}/analyses/turn-1", headers=as_new_user, json={"title": "My Amhara map"}
    )
    again = await analysis_names.name_turn(wid, "turn-1", "Which zones see onset?", model)
    nothing_made = await analysis_names.name_turn(wid, "turn-2", "Hello?", model)
    detail = (await client.get(f"/workspaces/{wid}", headers=as_new_user)).json()
    cleared = await client.patch(
        f"/workspaces/{wid}/analyses/turn-1", headers=as_new_user, json={"title": None}
    )

    assert named == {
        "turn_id": "turn-1",
        "title": "Amhara zones: FuXi onset, July 2024",
        "named_by": "assistant",
    }
    assert renamed.json()["named_by"] == "user"
    assert again is None and nothing_made is None
    assert detail["analyses"] == [
        {"turn_id": "turn-1", "title": "My Amhara map", "named_by": "user"}
    ]
    assert cleared.json() is None
    assert await workspaces.list_analyses(wid) == []


@pytest.mark.asyncio
async def test_a_new_analysis_name_reaches_an_open_workspace_page(
    client, as_new_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from ai_almanac.server.routers import workspaces as workspaces_router

    monkeypatch.setattr(workspaces_router, "EVENTS_POLL_SECONDS", 0.01)
    monkeypatch.setattr(workspaces_router, "EVENTS_LIFETIME_SECONDS", 0.3)
    workspace, _ = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]

    async def name_while_the_page_is_open() -> None:
        await asyncio.sleep(0.1)
        await workspaces.name_analysis(wid, "turn-1", "Amhara onset", "assistant")

    naming = asyncio.create_task(name_while_the_page_is_open())
    async with client.stream("GET", f"/workspaces/{wid}/events", headers=as_new_user) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])
    await naming

    [named] = [e for e in sse_events(body) if e["type"] == "analyses"]
    assert named["analyses"] == [
        {"turn_id": "turn-1", "title": "Amhara onset", "named_by": "assistant"}
    ]


@pytest.mark.parametrize(
    ("reply", "name"),
    [
        ("**Amhara zones: FuXi onset**\n\nThese results show…", "Amhara zones: FuXi onset"),
        ("“India: onset within two weeks”.", "India: onset within two weeks"),
        ("", None),
        ("x" * 200, None),
    ],
)
def test_the_name_is_read_from_the_first_line_of_the_reply(reply: str, name: str | None) -> None:
    assert analysis_names.parse_name(reply) == name


@pytest.mark.parametrize(
    ("given", "name"),
    [
        (["Onset skill by zone, Amhara"], "Onset skill by zone, Amhara"),
        (
            [
                "Onset skill by zone, Amhara",
                "Onset error map, Amhara",
                "Onset skill by zone, Amhara",
            ],
            "Onset skill by zone, Amhara (+1 more)",
        ),
        (["x" * 78, "Onset error map"], "x" * 78),
        ([], None),
        (["  "], None),
        (["x" * 200], None),
    ],
)
def test_an_analysis_takes_its_name_from_what_its_results_were_called(
    given: list[str], name: str | None
) -> None:
    assert analysis_names.name_from_results(given) == name


@pytest.mark.asyncio
async def test_an_analysis_is_named_by_what_the_assistant_called_its_result_not_the_question(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await _made_by_turn(
        user_id,
        wid,
        session["id"],
        "turn-1",
        [
            ledger.OutputFile(
                path=write_png(tmp_path / "fuxi_rain.png"),
                label="fuxi_rain",
                title="FuXi rainfall over Ethiopia, June 2024",
            ),
            ledger.OutputFile(
                path=write_nc(tmp_path / "outlines.nc"),
                label="outlines",
                title="Ethiopia area outlines",
                supporting=True,
            ),
        ],
    )
    await _made_by_turn(
        user_id,
        wid,
        session["id"],
        "turn-2",
        [ledger.OutputFile(path=write_nc(tmp_path / "rain.nc", seed=2.0), label="rain")],
    )
    question = "Show me the raw rainfall over ethiopia from fuxi for june 2024"
    asked = TestModel(custom_output_text="Ethiopia: FuXi rainfall")

    titled = await analysis_names.name_turn(wid, "turn-1", question, None)
    untitled_without_a_model = await analysis_names.name_turn(wid, "turn-2", question, None)
    untitled = await analysis_names.name_turn(wid, "turn-2", question, asked)

    assert titled == {
        "turn_id": "turn-1",
        "title": "FuXi rainfall over Ethiopia, June 2024",
        "named_by": "assistant",
    }
    assert untitled_without_a_model is None
    assert untitled is not None and untitled["title"] == "Ethiopia: FuXi rainfall"


@pytest.mark.asyncio
async def test_a_workspace_turn_asks_for_its_results_to_be_named_before_the_stream_ends(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from ai_almanac.server.services import llm
    from ai_almanac.server.services.chat_state import ChatScope
    from ai_almanac.settings import settings

    async def answer(_messages, _info: AgentInfo):
        yield "Done."

    asked: list[tuple[str, str | None]] = []
    monkeypatch.setattr(llm, "_build_model", lambda: FunctionModel(stream_function=answer))
    monkeypatch.setattr(settings, "name_workspace_analyses", True)
    monkeypatch.setattr(
        analysis_names,
        "name_turn_later",
        lambda workspace_id, turn_id, question, model: asked.append((workspace_id, question)),
    )
    workspace, session = await workspace_with_session(client, as_new_user)
    scope = ChatScope(kind="workspace", key=workspace["id"], job_ids=[])

    stream = llm.stream_response(
        [], user_id, session["id"], scope, latest_user_message="Map onset by state"
    )
    async for event in stream:
        if '"type": "done"' in event:
            break  # the client closes the stream here
    await stream.aclose()

    assert asked == [(workspace["id"], "Map onset by state")]
