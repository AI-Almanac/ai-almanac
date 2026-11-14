"""A run that names its own place is scored there, whatever region its data was filed under."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from ai_almanac.server import tables
from ai_almanac.server.db import get_db
from ai_almanac.server.services import boundaries
from ai_almanac.server.services.focus_area import (
    FocusBox,
    FocusUnits,
    area_bounds,
    parse_focus_area,
)
from ai_almanac.server.services.scoring_region import PlaceNotScorable, scored_by_place

DATA = {"lat_min": 3.0, "lat_max": 15.0, "lon_min": 33.0, "lon_max": 48.0}


def _square(name: str, lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> dict:
    ring = [
        [lon_min, lat_min],
        [lon_max, lat_min],
        [lon_max, lat_max],
        [lon_min, lat_max],
        [lon_min, lat_min],
    ]
    return {
        "type": "Feature",
        "properties": {"shapeName": name},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _outlined(place: FocusUnits, *squares: dict) -> FocusUnits:
    return place.model_copy(
        update={"geometry": {"type": "FeatureCollection", "features": list(squares)}}
    )


def _settings(place: FocusUnits) -> dict:
    return {"wet_spell": 3, "focus_area": place.model_dump(mode="json")}


# --- the place itself ----------------------------------------------------------


def test_a_place_names_its_country_by_three_letter_code() -> None:
    place = parse_focus_area({"level": "adm1", "units": ["Turkana"], "iso": " ken "})

    assert isinstance(place, FocusUnits)
    assert place.iso == "KEN"
    with pytest.raises(ValidationError, match="three-letter code"):
        parse_focus_area({"level": "adm1", "units": ["Turkana"], "iso": "Kenya"})


def test_a_whole_country_can_be_picked() -> None:
    country = parse_focus_area({"level": "adm0", "units": ["Kenya"], "iso": "KEN"})

    assert isinstance(country, FocusUnits)
    assert country.level == "adm0"


def test_an_area_is_enclosed_by_the_box_around_all_its_outlines() -> None:
    two_units = _outlined(
        FocusUnits(level="adm1", units=["West", "East"], iso="KEN"),
        _square("West", 1, 3, 34, 36),
        _square("East", 2, 5, 38, 41),
    )
    box = FocusBox(lat_min=8, lat_max=12, lon_min=38, lon_max=40)

    assert area_bounds(two_units) == {"lat_min": 1, "lat_max": 5, "lon_min": 34, "lon_max": 41}
    assert area_bounds(box) == {"lat_min": 8, "lat_max": 12, "lon_min": 38, "lon_max": 40}


@pytest.mark.asyncio
async def test_a_place_with_its_own_country_needs_no_region_for_its_outlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, str]] = []

    async def kenya(iso: str, level: str) -> dict:
        asked.append((iso, level))
        features = [_square("Turkana", 2, 5, 34, 36), _square("Marsabit", 2, 4, 36, 39)]
        return {"geojson": {"type": "FeatureCollection", "features": features}}

    monkeypatch.setattr(boundaries, "load_boundary", kenya)

    outlined = await boundaries.attach_unit_outlines(
        FocusUnits(level="adm1", units=["Turkana"], iso="KEN"), None
    )

    assert asked == [("KEN", "adm1")]
    assert [f["properties"]["name"] for f in outlined.geometry["features"]] == ["Turkana"]


@pytest.mark.asyncio
async def test_a_country_is_outlined_whole_whatever_name_it_was_picked_by(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def kenya(iso: str, level: str) -> dict:
        country = _square("Republic of Kenya", -5, 5, 34, 42)
        return {"geojson": {"type": "FeatureCollection", "features": [country]}}

    monkeypatch.setattr(boundaries, "load_boundary", kenya)

    outlined = await boundaries.attach_unit_outlines(
        FocusUnits(level="adm0", units=["Kenya"], iso="KEN"), None
    )

    assert area_bounds(outlined) == {"lat_min": -5, "lat_max": 5, "lon_min": 34, "lon_max": 42}


# --- what ROMP is given --------------------------------------------------------


def test_units_in_a_country_romp_knows_keep_its_region_and_are_masked_to_their_outlines() -> None:
    awi = _outlined(
        FocusUnits(level="adm2", units=["Awi"], iso="ETH"), _square("Awi", 10, 11, 36, 37)
    )

    scored = scored_by_place(_settings(awi), awi, DATA, DATA)

    assert scored["region"] == "Ethiopia"
    assert scored["focus_area"]["units"] == ["Awi"]
    assert "lat_min" not in scored


def test_a_whole_country_romp_knows_is_scored_exactly_as_its_region_always_was() -> None:
    india = _outlined(
        FocusUnits(level="adm0", units=["India"], iso="IND"), _square("India", 6, 36, 68, 98)
    )

    scored = scored_by_place(_settings(india), india, DATA, DATA)

    assert scored == {"wet_spell": 3, "region": "India"}


def test_a_place_anywhere_else_is_scored_on_its_own_box_within_the_data() -> None:
    turkana = _outlined(
        FocusUnits(level="adm1", units=["Turkana"], iso="KEN"), _square("Turkana", 1, 5, 30, 36)
    )

    scored = scored_by_place(_settings(turkana), turkana, DATA, DATA)

    assert scored["region"] == "custom"
    assert {edge: scored[edge] for edge in DATA} == {
        "lat_min": 3.0,
        "lat_max": 5.0,
        "lon_min": 33.0,
        "lon_max": 36.0,
    }
    assert scored["focus_area"]["units"] == ["Turkana"]
    assert (scored["land_only"], scored["shp_only"]) == (False, False)


def test_a_place_cannot_be_scored_against_data_of_unknown_extent() -> None:
    turkana = _outlined(
        FocusUnits(level="adm1", units=["Turkana"], iso="KEN"), _square("Turkana", 3, 5, 34, 36)
    )

    with pytest.raises(PlaceNotScorable, match="spatial bounds of both data sources"):
        scored_by_place(_settings(turkana), turkana, DATA, None)


# --- through a submission ------------------------------------------------------


async def _ethiopia_sources(client) -> tuple[str, str]:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"
    obs = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Highland observations",
            "path": str(root / "obs"),
            "region": "ethiopia",
            "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
        },
    )
    model = await client.post(
        "/data-sources",
        json={
            "kind": "model",
            "name": "Highland forecast",
            "path": str(root / "fuxi"),
            "region": "ethiopia",
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp", "model_type": "AIWP"},
        },
    )
    return obs.json()["id"], model.json()["id"]


async def _job_config(job_id: str) -> dict:
    async with get_db() as conn:
        raw = (
            await conn.execute(
                sa.select(tables.jobs.c.config_json).where(tables.jobs.c.id == job_id)
            )
        ).scalar_one()
    return json.loads(raw)


async def _submit(client, headers, monkeypatch: pytest.MonkeyPatch, place: dict, unit: dict):
    async def boundary(iso: str, level: str) -> dict:
        return {"geojson": {"type": "FeatureCollection", "features": [unit]}}

    async def no_launch(job_id: str) -> None:
        return None

    monkeypatch.setattr(boundaries, "load_boundary", boundary)
    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", no_launch)
    obs_id, model_id = await _ethiopia_sources(client)
    return await client.post(
        "/jobs",
        headers=headers,
        json={"dataset_id": obs_id, "model_name": model_id, "params": {"focus_area": place}},
    )


@pytest.mark.asyncio
async def test_a_run_in_a_place_romp_does_not_know_is_recorded_under_the_place(
    client, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    place = {"level": "adm1", "units": ["Borderland"], "iso": "KEN"}

    response = await _submit(
        client, as_new_user, monkeypatch, place, _square("Borderland", 8, 9, 38, 39)
    )

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["region_id"], job["region_name"], job["romp_region"]) == (
        None,
        "Borderland",
        "custom",
    )
    params = (await _job_config(job["id"]))["romp_params"]
    assert params["region"] == "custom"
    assert 8 <= params["lat_min"] < params["lat_max"] <= 9
    assert 38 <= params["lon_min"] < params["lon_max"] <= 39
    assert params["focus_area"]["iso"] == "KEN"


@pytest.mark.asyncio
async def test_a_run_in_a_place_the_data_does_not_reach_is_refused(
    client, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    place = {"level": "adm1", "units": ["Dakar"], "iso": "SEN"}

    response = await _submit(
        client, as_new_user, monkeypatch, place, _square("Dakar", 14, 15, -18, -17)
    )

    assert response.status_code == 400
    assert "Dakar does not overlap" in response.json()["detail"]
