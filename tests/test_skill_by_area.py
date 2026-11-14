"""A benchmark's gridded skill, averaged over the areas a forecaster names."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from ai_almanac.paths import database_path
from ai_almanac.server.services import skill_area_summaries as skill
from ai_almanac.server.services import skill_by_area, workspaces
from ai_almanac.server.services.area_summaries import Area
from ai_almanac.server.services.storage import get_storage
from tests.helpers.places import REGIONS, ZONES, serve_boundaries
from tests.helpers.records import insert_job
from tests.helpers.skill_runs import (
    AMHARA_ZONES,
    workspace_with_zone_run,
    write_skill,
    zones_config,
)
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import conversation_in, workspace_with_session


def _ring(lon0: float, lat0: float, lon1: float, lat1: float) -> list:
    return [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]


def _polygon(*rings: list) -> dict:
    return {"type": "Polygon", "coordinates": list(rings)}


# Cells centred on lat 0..3 and lon 10..13; a cell's error is 4 * its row + its column.
LAT = np.array([0.0, 1.0, 2.0, 3.0])
LON = np.array([10.0, 11.0, 12.0, 13.0])
ERROR = np.arange(16, dtype=float).reshape(4, 4)

WHOLE_GRID_WITH_A_HOLE = Area(
    "Ring", None, _polygon(_ring(9.5, -0.5, 13.5, 3.5), _ring(10.5, 0.5, 12.5, 2.5))
)
BETWEEN_CELL_CENTRES = Area("Tiny", "Ring", _polygon(_ring(10.1, 0.1, 10.4, 0.4)))
TWO_CORNERS = Area(
    "Corners",
    None,
    {
        "type": "MultiPolygon",
        "coordinates": [[_ring(9.5, -0.5, 10.5, 0.5)], [_ring(12.5, -0.5, 13.5, 0.5)]],
    },
)


def _grid(**metrics: np.ndarray) -> skill.SkillGrid:
    return skill.SkillGrid(LAT, LON, metrics)


def test_an_area_takes_the_mean_of_the_cells_centred_in_it_and_a_hole_is_left_out() -> None:
    [ring] = skill.area_means(_grid(mean_mae=ERROR), [WHOLE_GRID_WITH_A_HOLE])

    # All 16 cells sum to 120; the hole removes cells 5, 6, 9 and 10.
    assert ring.cells == 12
    assert ring.means == {"mean_mae": (120 - 30) / 12}


def test_an_area_holding_no_cell_centre_has_no_value_rather_than_zero() -> None:
    [tiny] = skill.area_means(_grid(mean_mae=ERROR, miss_rate=ERROR / 100), [BETWEEN_CELL_CENTRES])

    assert tiny.cells == 0
    assert tiny.means == {"mean_mae": None, "miss_rate": None}


def test_the_parts_of_a_multipolygon_add_up() -> None:
    [corners] = skill.area_means(_grid(mean_mae=ERROR), [TWO_CORNERS])

    assert (corners.cells, corners.means["mean_mae"]) == (2, (0 + 3) / 2)


def test_cells_that_were_not_scored_drop_out_of_the_mean_and_the_count() -> None:
    unscored_corner = ERROR.copy()
    unscored_corner[0, 0] = np.nan
    nothing_scored = np.full_like(ERROR, np.nan)

    [partly] = skill.area_means(_grid(mean_mae=unscored_corner), [TWO_CORNERS])
    [empty] = skill.area_means(_grid(mean_mae=nothing_scored), [TWO_CORNERS])

    assert (partly.cells, partly.means["mean_mae"]) == (1, 3.0)
    assert (empty.cells, empty.means["mean_mae"]) == (0, None)


def test_a_grid_on_0_to_360_longitudes_meets_outlines_on_minus_180_to_180() -> None:
    dataset = xr.Dataset(
        {"mean_mae": (("lon", "lat"), np.array([[1.0], [5.0]]))},
        coords={"lat": [10.0], "lon": [350.0, 351.0]},
    )
    west_of_greenwich = Area("West", None, _polygon(_ring(-9.5, 9.5, -8.5, 10.5)))

    [west] = skill.area_means(skill.parse_skill_grid(dataset), [west_of_greenwich])

    assert (west.cells, west.means["mean_mae"]) == (1, 5.0)


def test_the_table_sets_each_model_beside_the_baseline_and_lists_uncovered_areas_apart() -> None:
    table = skill.skill_by_area(
        [WHOLE_GRID_WITH_A_HOLE, BETWEEN_CELL_CENTRES],
        [
            skill.RunGrid("AIFS", "16-30", _grid(mean_mae=ERROR)),
            skill.RunGrid("AIFS", "1-15", _grid(mean_mae=ERROR, miss_rate=ERROR / 10)),
            skill.RunGrid("climatology", "1-15", _grid(mean_mae=ERROR * 2)),
        ],
        areas_from="boundaries",
        level="adm2",
    )

    assert (table.models, table.windows, table.not_covered) == (
        ["AIFS"],
        ["1-15", "16-30"],
        ["Tiny"],
    )
    first, later = table.rows
    assert (first.area, first.window, first.cells) == ("Ring", "1-15", 12)
    assert (first.onset_error_days, first.miss_rate, first.false_alarm_rate) == (7.5, 0.75, None)
    assert first.climatology is not None and first.climatology.onset_error_days == 15.0
    assert later.window == "16-30" and later.climatology is None


# --- a run in a workspace --------------------------------------------------------


@pytest.fixture
def ethiopia_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    serve_boundaries(monkeypatch, {"adm1": REGIONS, "adm2": ZONES})


@pytest.mark.asyncio
async def test_a_run_is_summarized_over_its_own_zones_with_the_baseline_alongside(
    client, as_new_user, user_id
) -> None:
    wid, run = await workspace_with_zone_run(client, as_new_user, user_id)

    response = await client.get(f"/workspaces/{wid}/runs/{run}/skill-by-area", headers=as_new_user)

    assert response.status_code == 200, response.text
    table = response.json()
    assert (table["areas_from"], table["level"]) == ("area_of_interest", "adm2")
    assert (table["models"], table["windows"], table["not_covered"]) == (["AIFS"], ["1-15"], [])
    assert table["rows"] == [
        {
            "area": "East Gojjam",
            "parent": None,
            "model": "AIFS",
            "window": "1-15",
            "cells": 4,
            "onset_error_days": 4.0,
            "false_alarm_rate": 0.4,
            "miss_rate": 0.2,
            "climatology": {
                "cells": 4,
                "onset_error_days": 8.0,
                "false_alarm_rate": 0.8,
                "miss_rate": 0.4,
            },
        },
        {
            "area": "North Gondar",
            "parent": None,
            "model": "AIFS",
            "window": "1-15",
            "cells": 2,
            "onset_error_days": 8.0,
            "false_alarm_rate": 0.8,
            "miss_rate": 0.4,
            "climatology": {
                "cells": 2,
                "onset_error_days": 16.0,
                "false_alarm_rate": 1.6,
                "miss_rate": 0.8,
            },
        },
    ]


@pytest.mark.asyncio
async def test_another_level_takes_its_areas_from_the_regions_boundaries(
    client, as_new_user, user_id, ethiopia_boundaries
) -> None:
    wid, run = await workspace_with_zone_run(client, as_new_user, user_id)

    table = await skill_by_area.summarize_skill_by_area(wid, run, level="adm1")
    zone = await skill_by_area.summarize_skill_by_area(wid, run, level="adm2", within="Amhara")

    assert (table.areas_from, table.not_covered) == ("boundaries", ["Oromia"])
    [amhara] = table.rows
    assert (amhara.area, amhara.cells, amhara.onset_error_days) == ("Amhara", 9, 5.0)
    assert [(row.area, row.parent) for row in zone.rows] == [
        ("East Gojjam", "Amhara"),
        ("North Gondar", "Amhara"),
    ]


@pytest.mark.asyncio
async def test_a_run_that_is_unknown_unfinished_or_someone_elses_is_refused(
    client, as_new_user, user_id
) -> None:
    wid, run = await workspace_with_zone_run(client, as_new_user, user_id)
    queued = insert_job(user_id, zones_config(), status="queued")
    await workspaces.link_job(wid, queued)
    unlinked = insert_job(user_id, zones_config())
    someone_else = {"X-Forwarded-User": f"pytest-{uuid.uuid4()}"}

    async def status_of(run_id: str, headers: dict | None = None) -> tuple[int, str]:
        response = await client.get(
            f"/workspaces/{wid}/runs/{run_id}/skill-by-area", headers=headers or as_new_user
        )
        return response.status_code, response.text

    assert (await status_of(unlinked))[0] == 404
    assert (await status_of(run, someone_else))[0] == 404
    code, detail = await status_of(queued)
    assert code == 400 and "has not finished" in detail
    unknown_level = await client.get(
        f"/workspaces/{wid}/runs/{run}/skill-by-area?level=adm9", headers=as_new_user
    )
    assert unknown_level.status_code == 422


# --- files that cannot be read, and models that are missing -----------------------


def _skill_file_of(job_id: str) -> Path:
    output, _ = get_storage().job_output_uri(job_id)
    return Path(output) / "spatial_metrics_aifs_1-15.nc"


async def _read_failure(client, as_new_user, user_id, wid: str, run: str) -> tuple[dict, dict]:
    """What the page and the assistant are each told when the run's files cannot be read."""

    from ai_almanac.server.services.primitives import analysis

    response = await client.get(f"/workspaces/{wid}/runs/{run}/skill-by-area", headers=as_new_user)
    assert response.status_code == 500, response.text
    ctx = tool_ctx(user_id, await conversation_in(client, as_new_user, wid), wid)
    return response.json(), await analysis.summarize_skill_by_area(ctx, job_id=run)


def _assert_says_only_that_the_maps_could_not_be_read(told: dict, job_id: str) -> None:
    text = json.dumps(told)
    assert skill.SKILL_MAPS_UNREADABLE in text
    assert job_id not in text and "spatial_metrics" not in text and "/" not in text


def _damage(job_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _skill_file_of(job_id).write_bytes(b"not a NetCDF file")


def _lose(job_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(path):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{path}'")

    monkeypatch.setattr(type(get_storage()), "open_nc_dataset", lambda self, path: gone(path))


def _outgrow_the_load_limit(job_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_almanac.server.services import storage as storage_module

    monkeypatch.setattr(storage_module, "_MAX_EAGER_NC_BYTES", 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("spoil", [_damage, _lose, _outgrow_the_load_limit])
async def test_a_skill_file_that_cannot_be_read_is_reported_without_its_path(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch, spoil
) -> None:
    wid, run = await workspace_with_zone_run(client, as_new_user, user_id)
    spoil(run, monkeypatch)

    for told in await _read_failure(client, as_new_user, user_id, wid, run):
        _assert_says_only_that_the_maps_could_not_be_read(told, run)


@pytest.mark.asyncio
async def test_a_model_whose_run_failed_or_was_cancelled_is_named_as_not_shown(
    client, as_new_user, user_id
) -> None:
    wid, run = await workspace_with_zone_run(client, as_new_user, user_id)
    _set_run(run, "run-1")
    for model, status in (("FuXi", "failed"), ("GraphCast", "canceled"), ("Pangu", "running")):
        sibling = insert_job(
            user_id,
            {**zones_config(), "model_name": model.lower(), "model_display_name": model},
            status=status,
        )
        _set_run(sibling, "run-1")
        await workspaces.link_job(wid, sibling)

    table = (
        await client.get(f"/workspaces/{wid}/runs/run-1/skill-by-area", headers=as_new_user)
    ).json()

    assert table["models"] == ["AIFS"]
    assert table["not_shown"] == [
        {"model": "FuXi", "reason": "its run failed"},
        {"model": "GraphCast", "reason": "its run was cancelled"},
        {"model": "Pangu", "reason": "its run has not finished"},
    ]


def _set_run(job_id: str, run_id: str) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute("UPDATE jobs SET run_id = ? WHERE id = ?", (run_id, job_id))
        conn.commit()


def test_a_finished_model_with_no_skill_maps_is_not_shown_and_a_shown_one_is_not_listed() -> None:
    jobs = [
        {"status": "complete", "config": {"model_name": "aifs", "model_display_name": "AIFS"}},
        {"status": "complete", "config": {"model_name": "gencast"}},
        {"status": "failed", "config": {"model_name": "aifs", "model_display_name": "AIFS"}},
    ]

    missing = skill.models_not_shown(jobs, ["AIFS"])

    assert [m.model_dump() for m in missing] == [
        {"model": "gencast", "reason": "its run wrote no skill maps"}
    ]


@pytest.mark.asyncio
async def test_frozen_outlines_without_names_fall_back_to_the_areas_the_run_named(
    client, as_new_user, user_id, ethiopia_boundaries
) -> None:
    config = zones_config()
    for feature in config["romp_params"]["focus_area"]["geometry"]["features"]:
        feature["properties"] = {}
    workspace, _ = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, config)
    write_skill(job_id, "aifs", 1.0)
    await workspaces.link_job(workspace["id"], job_id)

    table = await skill_by_area.summarize_skill_by_area(workspace["id"], job_id)

    assert [row.area for row in table.rows] == AMHARA_ZONES


def test_more_areas_than_one_table_holds_are_refused_in_plain_words() -> None:
    skill.check_area_count(skill.MAX_AREAS)

    with pytest.raises(skill.SkillByAreaError, match="Narrow it to the areas inside one larger"):
        skill.check_area_count(skill.MAX_AREAS + 1)


def _unfiltered_masks(lat: np.ndarray, lon: np.ndarray, areas: list) -> np.ndarray:
    from ai_almanac.server.services.focus_area import points_inside

    lat2d, lon2d = np.meshgrid(lat, lon, indexing="ij")
    return np.array([points_inside(area.geometry, lon2d, lat2d) for area in areas])


def test_testing_only_the_cells_near_an_area_finds_the_same_cells_as_testing_them_all() -> None:
    from ai_almanac.server.services.area_summaries import Area

    lat, lon = np.arange(3.0, 15.0, 0.25), np.arange(33.0, 48.0, 0.25)
    with_hole = {
        "type": "Polygon",
        "coordinates": [
            [[35, 8], [41, 8], [41, 13], [35, 13], [35, 8]],
            [[37, 10], [39, 10], [39, 11], [37, 11], [37, 10]],
        ],
    }
    two_parts = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[33.1, 3.1], [34.4, 3.3], [33.6, 5.2], [33.1, 3.1]]],
            [[[44, 12], [47.9, 12.2], [46, 14.9], [44, 12]]],
        ],
    }
    off_the_grid = {
        "type": "Polygon",
        "coordinates": [[[60, 20], [61, 20], [61, 21], [60, 20]]],
    }
    areas = [
        Area(name, None, shape)
        for name, shape in (("hole", with_hole), ("parts", two_parts), ("away", off_the_grid))
    ]

    masks = skill.cell_masks(lat, lon, areas)

    assert np.array_equal(masks, _unfiltered_masks(lat, lon, areas))
    assert masks[0].sum() > 0 and masks[1].sum() > 0 and masks[2].sum() == 0
