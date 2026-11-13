"""The assistant's way to a benchmark's skill by area: one call, cell counts included."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import skill_area_report, workspaces
from ai_almanac.server.services import skill_area_summaries as skill
from ai_almanac.server.services.primitives import analysis
from tests.helpers.records import insert_job
from tests.helpers.skill_runs import workspace_with_zone_run, zones_config
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import conversation_in


def _row(area: str, cells: int, baseline: skill.AreaSkill | None = None) -> skill.AreaSkillRow:
    return skill.AreaSkillRow(
        area=area,
        parent="Amhara",
        model="AIFS",
        window="1-15",
        cells=cells,
        onset_error_days=4.0,
        false_alarm_rate=0.4,
        miss_rate=0.2,
        climatology=baseline,
    )


def _table(rows: list[skill.AreaSkillRow]) -> skill.SkillByArea:
    return skill.SkillByArea(
        areas_from="boundaries",
        level="adm2",
        models=["AIFS"],
        windows=["1-15"],
        rows=rows,
        not_covered=["Wag Hemra"],
    )


def _tables(items: list[dict]) -> list[dict]:
    return [item for item in items if item["kind"] == "table"]


def test_the_quoted_table_names_its_columns_once_and_keeps_cells_and_uncovered_areas() -> None:
    baseline = skill.AreaSkill(onset_error_days=8.0, false_alarm_rate=0.8, miss_rate=0.4, cells=4)

    quoted = skill_area_report.quotable(_table([_row("East Gojjam", 4, baseline)]))

    assert quoted["columns"][:5] == ["area", "parent", "model", "window", "cells"]
    assert quoted["rows"] == [
        ["East Gojjam", "Amhara", "AIFS", "1-15", 4, 4.0, 0.4, 0.2, 8.0, 0.8, 0.4]
    ]
    assert quoted["not_covered"] == ["Wag Hemra"]
    assert "truncated" not in quoted


def test_a_long_table_is_cut_short_inline_and_says_so_while_the_file_keeps_every_row() -> None:
    table = _table([_row(f"Zone {i}", 1) for i in range(skill_area_report.INLINE_ROWS + 5)])

    quoted = skill_area_report.quotable(table)
    file_lines = skill_area_report.table_csv(table).splitlines()

    assert len(quoted["rows"]) == skill_area_report.INLINE_ROWS and quoted["truncated"] is True
    assert len(file_lines) == skill_area_report.INLINE_ROWS + 5 + 1
    assert file_lines[0].split(",") == list(skill_area_report.COLUMNS)
    assert file_lines[1].endswith(",,,"), "a run with no baseline leaves those columns empty"


@pytest.mark.asyncio
async def test_the_assistant_reads_a_runs_skill_by_zone_with_cell_counts_and_gets_a_file(
    client, as_new_user, user_id
) -> None:
    wid, job_id = await workspace_with_zone_run(client, as_new_user, user_id)
    ctx = tool_ctx(user_id, await conversation_in(client, as_new_user, wid), wid)

    result = await analysis.summarize_skill_by_area(
        ctx, job_id=job_id, name="Onset skill by zone, Amhara"
    )

    cells = {row[0]: row[result["columns"].index("cells")] for row in result["rows"]}
    assert cells == {"East Gojjam": 4, "North Gondar": 2}
    assert (result["areas_from"], result["not_covered"]) == ("area_of_interest", [])
    [table_file] = _tables(await workspaces.listing(wid))
    assert (table_file["title"], table_file["format"]) == ("Onset skill by zone, Amhara", "csv")


@pytest.mark.asyncio
async def test_a_run_outside_the_conversations_workspace_is_refused_without_naming_it(
    client, as_new_user, user_id
) -> None:
    wid, _ = await workspace_with_zone_run(client, as_new_user, user_id)
    elsewhere = insert_job(user_id, zones_config())
    ctx = tool_ctx(user_id, await conversation_in(client, as_new_user, wid), wid)

    unlinked = await analysis.summarize_skill_by_area(ctx, job_id=elsewhere)
    unknown = await analysis.summarize_skill_by_area(ctx, job_id="no-such-run")

    for refused in (unlinked, unknown):
        assert "not a benchmark run in this workspace" in refused["error"]
        assert "rows" not in refused
    assert elsewhere not in unlinked["error"]
    assert _tables(await workspaces.listing(wid)) == []


@pytest.mark.asyncio
async def test_a_conversation_outside_a_workspace_cannot_read_skill_by_area(
    client, as_new_user, user_id
) -> None:
    from types import SimpleNamespace

    from ai_almanac.server.services.chat_state import ChatScope
    from ai_almanac.server.services.llm import chat_deps

    wid, job_id = await workspace_with_zone_run(client, as_new_user, user_id)
    session_id = await conversation_in(client, as_new_user, wid)
    scope = ChatScope(kind="job_set", key="jobs", job_ids=[job_id])
    ctx = SimpleNamespace(deps=await chat_deps(user_id, session_id, scope))

    refused = await analysis.summarize_skill_by_area(ctx, job_id=job_id)

    assert refused["error"] == workspaces.NOT_IN_CONVERSATION
