"""A forecast's onset probabilities, summarized for the areas a forecaster names."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from ai_almanac.server.services import area_summaries, ledger, workspaces
from ai_almanac.server.services.area_summaries import Area
from ai_almanac.server.services.primitives import analysis, figures
from tests.helpers.files import write_nc
from tests.helpers.places import serve_boundaries
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session

WEEKLY_HEADER = "id,time,cv_week1,cv_week2,cv_week3,cv_week4,cv_later"


def _box(lon0: float, lat0: float, lon1: float, lat1: float) -> dict:
    ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
    return {"type": "Polygon", "coordinates": [ring]}


def _weekly(*rows: str) -> area_summaries.ForecastTable:
    return area_summaries.parse_forecast_table("\n".join([WEEKLY_HEADER, *rows]) + "\n")


def _summary(table, *areas: Area, within_days=(7, 14, 28)) -> list[dict]:
    return area_summaries.summarize_areas(
        table, area_summaries.area_weights(table, areas), within_days
    )


# Two 1-degree cells side by side, centred on (0, 0) and (0, 1).
TWO_CELLS = (
    "0.00_0.00,2026-06-01,0.9,0.05,0.02,0.02,0.01",
    "0.00_1.00,2026-06-01,0.3,0.3,0.2,0.1,0.1",
)


def test_a_cell_counts_by_how_much_of_the_area_it_covers_not_by_its_centre() -> None:
    # The zone covers all of the first cell and a quarter of the second, whose centre is outside.
    [row] = _summary(_weekly(*TWO_CELLS), Area("Zone", None, _box(-0.5, -0.5, 0.75, 0.5)))

    assert row["p_week1"] == pytest.approx(0.9 * 0.8 + 0.3 * 0.2)
    assert row["points"] == 2
    assert row["coverage"] == 1.0


def test_the_chance_within_two_weeks_adds_the_first_two_weekly_windows() -> None:
    [row] = _summary(_weekly(TWO_CELLS[1]), Area("Zone", None, _box(0.5, -0.5, 1.5, 0.5)))

    assert row["p_within_7d"] == pytest.approx(0.3)
    assert row["p_within_14d"] == pytest.approx(0.6)
    assert row["p_within_28d"] == pytest.approx(0.9)
    assert row["most_likely"] == "week1"


def test_an_area_reaching_past_the_forecast_reports_how_much_of_it_is_covered() -> None:
    [row] = _summary(_weekly(*TWO_CELLS), Area("Zone", None, _box(-0.5, -0.5, 2.5, 0.5)))

    assert row["coverage"] == pytest.approx(2 / 3, abs=1e-3)


def test_weekly_windows_cannot_answer_for_days_inside_a_week() -> None:
    table = _weekly(*TWO_CELLS)

    assert area_summaries.within_days_errors(table, [14]) == []
    [error] = area_summaries.within_days_errors(table, [10])
    assert "within 10 days" in error and "7, 14, 21, 28" in error


def test_a_cell_without_a_forecast_on_a_date_is_left_out_rather_than_read_as_no_chance() -> None:
    table = _weekly(*TWO_CELLS, "0.00_0.00,2026-06-04,0.5,0.2,0.1,0.1,0.1")

    rows = _summary(table, Area("Zone", None, _box(-0.5, -0.5, 1.5, 0.5)))

    later = next(row for row in rows if row["issue_date"] == "2026-06-04")
    assert later["points"] == 1
    assert later["p_week1"] == pytest.approx(0.5)


def test_an_area_smaller_than_a_cell_takes_the_cell_it_lies_in() -> None:
    [row] = _summary(_weekly(*TWO_CELLS), Area("Town", None, _box(0.09, 0.09, 0.11, 0.11)))

    assert row["p_week1"] == pytest.approx(0.9)


def test_named_unit_points_stand_for_their_own_area_or_average_into_a_larger_one() -> None:
    header = "id,time,lat,lon,cv_week1,cv_week2,cv_week3,cv_week4,cv_later"
    table = area_summaries.parse_forecast_table(
        f"{header}\nDessie Zuria,2026-06-01,11.1,39.6,0.6,0.2,0.1,0.05,0.05\n"
        "Kalu,2026-06-01,10.9,39.8,0.2,0.4,0.2,0.1,0.1\n"
    )

    [woreda] = _summary(table, Area("Dessie Zuria", "South Wello", _box(0, 0, 1, 1)))
    [zone] = _summary(table, Area("South Wello", "Amhara", _box(39, 10, 40, 12)))

    assert woreda["p_week1"] == pytest.approx(0.6)
    assert zone["p_week1"] == pytest.approx(0.4)
    assert zone["coverage"] is None


def test_a_table_without_onset_probabilities_is_refused() -> None:
    with pytest.raises(ValueError, match="no onset probabilities"):
        area_summaries.parse_forecast_table("id,time,far\n0.00_0.00,2026-06-01,0.1\n")


ZONES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"shapeName": "West"},
            "geometry": _box(-0.5, -0.5, 0.5, 0.5),
        },
        {
            "type": "Feature",
            "properties": {"shapeName": "East"},
            "geometry": _box(0.5, -0.5, 1.5, 0.5),
        },
    ],
}
REGIONS = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"shapeName": "Amhara"}, "geometry": _box(-1, -1, 2, 1)}
    ],
}


@pytest.fixture
def ethiopia_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    serve_boundaries(monkeypatch, {"adm1": REGIONS, "adm2": ZONES})


async def _forecast_table(user_id: str, wid: str, tmp_path: Path) -> None:
    path = tmp_path / "blended_forecast_probabilities.csv"
    path.write_text("\n".join([WEEKLY_HEADER, *TWO_CELLS]) + "\n")
    _, [artifact] = await ledger.write_step(
        ledger.StepDraft(primitive="fetch.test", version="1.0.0", params={}),
        ledger.Attribution.by_user(user_id),
        [ledger.OutputFile(path=path, label="forecast", kind="table")],
    )
    await workspaces.add_artifacts(wid, [(artifact, "forecast")])


@pytest.mark.asyncio
async def test_a_zone_table_and_its_outlines_enter_the_workspace_and_map_together(
    client, as_new_user, user_id, tmp_path: Path, ethiopia_boundaries
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    await _forecast_table(user_id, workspace["id"], tmp_path)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    summary = await analysis.summarize_by_area(
        ctx, forecast="forecast", region_id="ethiopia", within="Amhara", name="Amhara zones"
    )

    assert summary.get("error") is None, summary
    assert [(row["area"], row["parent"]) for row in summary["latest"]] == [
        ("East", "Amhara"),
        ("West", "Amhara"),
    ]
    table, outlines = summary["workspace_artifacts"]
    assert (table["format"], outlines["format"]) == ("csv", "geojson")
    shown = {item["id"]: item for item in await workspaces.listing(workspace["id"])}
    assert (shown[table["id"]]["title"], shown[table["id"]]["supporting"]) == (
        "Amhara zones",
        False,
    )
    assert shown[outlines["id"]]["supporting"] is True
    steps = await ledger.user_steps(user_id)
    step = next(s for s in steps.values() if s["primitive"] == "forecast.summarize_by_area")
    assert step["params"]["single_run"] is False, "a blend's table holds chances, not shares"
    stored = ledger.local_path(await workspaces.resolve(workspace["id"], outlines["label"]))
    assert json.loads(stored.read_text())["source"]["boundaryID"] == "ETH-ADM2-test"
    rows = list(
        csv.DictReader(
            io.StringIO(
                ledger.local_path(
                    await workspaces.resolve(workspace["id"], table["label"])
                ).read_text()
            )
        )
    )
    assert {row["area"]: float(row["p_week1"]) for row in rows} == {"West": 0.9, "East": 0.3}

    again = await analysis.summarize_by_area(ctx, forecast="forecast", region_id="ethiopia")
    assert again["workspace_artifacts"][1]["id"] == outlines["id"]

    mapped = await figures.plot_area_map(ctx, table=table["label"], areas=outlines["label"])
    assert mapped.get("error") is None, mapped
    assert mapped["workspace_artifacts"][0]["kind"] == "figure"


@pytest.mark.asyncio
async def test_unknown_areas_and_unanswerable_windows_are_refused(
    client, as_new_user, user_id, tmp_path: Path, ethiopia_boundaries
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    await _forecast_table(user_id, workspace["id"], tmp_path)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    unknown = await analysis.summarize_by_area(
        ctx, forecast="forecast", region_id="ethiopia", areas=["Atlantis"]
    )
    days = await analysis.summarize_by_area(
        ctx, forecast="forecast", region_id="ethiopia", within_days=[10]
    )

    assert "Atlantis" in unknown["error"]
    assert "within 10 days" in days["error"]


@pytest.mark.asyncio
async def test_a_gridded_map_can_carry_the_zone_outlines(
    client, as_new_user, user_id, tmp_path: Path, ethiopia_boundaries
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    await _forecast_table(user_id, workspace["id"], tmp_path)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    summary = await analysis.summarize_by_area(ctx, forecast="forecast", region_id="ethiopia")
    outlines = summary["workspace_artifacts"][1]["label"]
    _, [field] = await ledger.write_step(
        ledger.StepDraft(primitive="fetch.test", version="1.0.0", params={}),
        ledger.Attribution.by_user(user_id),
        [ledger.OutputFile(path=write_nc(tmp_path / "obs.nc"), label="obs", kind="dataset")],
    )
    await workspaces.add_artifacts(workspace["id"], [(field, "obs")])

    plotted = await figures.plot_map(ctx, artifact="obs", variable="far", areas=outlines)
    not_outlines = await figures.plot_map(ctx, artifact="obs", variable="far", areas="obs")

    assert plotted.get("error") is None, plotted
    assert "not area outlines" in not_outlines["error"]
