"""Data sources are scored together where both have data, not by a shared region label."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_almanac.server.services import coverage
from ai_almanac.server.services.registry import CatalogSnapshot

EAST_AFRICA = {"lat_min": -12.0, "lat_max": 18.0, "lon_min": 28.0, "lon_max": 52.0}
ETHIOPIA = {"lat_min": 3.0, "lat_max": 15.0, "lon_min": 33.0, "lon_max": 48.0}
INDIA = {"lat_min": 6.0, "lat_max": 36.0, "lon_min": 68.0, "lon_max": 98.0}


def _source(name: str, region: str | None, bounds: dict | None) -> dict:
    metadata = {"spatial_bounds": bounds} if bounds else {}
    return {"id": name.lower(), "name": name, "region": region, "metadata": metadata}


def _registry_model(name: str, region: str | None, bounds: dict | None) -> dict:
    """A model as the catalog lists it: the row's metadata at the top level."""
    return {
        "id": name.lower(),
        "display_name": name,
        "region": region or "",
        "spatial_bounds": bounds,
    }


def test_an_archive_covering_more_than_one_region_is_scored_with_any_observations_inside_it() -> (
    None
):
    observations = _source("Highland gauges", "ethiopia", ETHIOPIA)
    archive = _source("East Africa hindcasts", None, EAST_AFRICA)

    assert coverage.scored_together(observations, archive)


def test_sources_filed_under_one_region_are_refused_when_their_data_does_not_meet() -> None:
    observations = _source("Highland gauges", "ethiopia", ETHIOPIA)
    misfiled = _source("Monsoon hindcasts", "ethiopia", INDIA)

    assert coverage.mismatch(observations, misfiled) == (
        "Monsoon hindcasts has no data where Highland gauges does."
    )


@pytest.mark.parametrize(
    ("model_region", "paired"), [("ethiopia", True), ("india", False), (None, False)]
)
def test_a_source_of_unknown_extent_is_matched_by_the_region_it_was_filed_under(
    model_region: str | None, paired: bool
) -> None:
    observations = _source("Highland gauges", "ethiopia", ETHIOPIA)
    older = _source("Older hindcasts", model_region, None)

    assert coverage.scored_together(observations, older) is paired


def test_data_reaches_an_area_only_when_every_source_has_data_in_it() -> None:
    gauges = _source("Highland gauges", "ethiopia", ETHIOPIA)
    monsoon = _source("Monsoon hindcasts", "india", INDIA)
    unknown = _source("Older hindcasts", "ethiopia", None)

    assert coverage.reaches(ETHIOPIA, gauges, unknown)
    assert not coverage.reaches(ETHIOPIA, gauges, monsoon)
    assert coverage.reaches({"display_name": "No box of its own"}, monsoon)


def test_models_are_offered_for_the_chosen_observations_wherever_they_were_filed() -> None:
    catalog = CatalogSnapshot(
        regions=(),
        models=(
            _registry_model("Regional", "ethiopia", ETHIOPIA),
            _registry_model("Global", None, EAST_AFRICA),
            _registry_model("Elsewhere", "india", INDIA),
        ),
        observations=(_source("gauges", "ethiopia", ETHIOPIA),),
    )

    offered = [model["display_name"] for model in catalog.models_for("gauges", "ethiopia")]

    assert offered == ["Regional", "Global"]


def test_before_observations_are_chosen_models_are_offered_by_region() -> None:
    catalog = CatalogSnapshot(
        regions=(),
        models=(
            _registry_model("Regional", "ethiopia", ETHIOPIA),
            _registry_model("Global", None, EAST_AFRICA),
        ),
    )

    offered = [model["display_name"] for model in catalog.models_for(None, "ethiopia")]

    assert offered == ["Regional"]


# --- through a submission ------------------------------------------------------


async def _submit(
    client,
    headers,
    monkeypatch: pytest.MonkeyPatch,
    *,
    observations_region: str | None,
    model_region: str | None,
    params: dict | None = None,
):
    async def no_launch(job_id: str) -> None:
        return None

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", no_launch)
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"
    obs = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Highland observations",
            "path": str(root / "obs"),
            "region": observations_region,
            "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
        },
    )
    model = await client.post(
        "/data-sources",
        json={
            "kind": "model",
            "name": "Unfiled forecast",
            "path": str(root / "fuxi"),
            "region": model_region,
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp", "model_type": "AIWP"},
        },
    )
    return await client.post(
        "/jobs",
        headers=headers,
        json={
            "dataset_id": obs.json()["id"],
            "model_name": model.json()["id"],
            "params": params or {},
        },
    )


@pytest.mark.asyncio
async def test_a_forecast_source_filed_under_no_region_is_scored_in_its_observations_region(
    client, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    response = await _submit(
        client, as_new_user, monkeypatch, observations_region="ethiopia", model_region=None
    )

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["region_id"], job["romp_region"]) == ("ethiopia", "Ethiopia")


@pytest.mark.asyncio
async def test_sources_filed_under_no_region_are_scored_where_their_data_overlaps(
    client, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    response = await _submit(
        client, as_new_user, monkeypatch, observations_region=None, model_region=None
    )

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["region_id"], job["romp_region"]) == (None, "custom")
    params = job["params"]
    assert params["lat_min"] < params["lat_max"] and params["lon_min"] < params["lon_max"]


@pytest.mark.asyncio
async def test_a_region_the_data_does_not_reach_is_refused_before_anything_runs(
    client, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    response = await _submit(
        client,
        as_new_user,
        monkeypatch,
        observations_region=None,
        model_region=None,
        params={"region": "india"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "The selected data does not reach India."


async def _unfiled_sources(client) -> tuple[str, str]:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"
    obs = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Unfiled observations",
            "path": str(root / "obs"),
            "region": None,
            "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
        },
    )
    model = await client.post(
        "/data-sources",
        json={
            "kind": "model",
            "name": "Unfiled forecast",
            "path": str(root / "fuxi"),
            "region": None,
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp", "model_type": "AIWP"},
        },
    )
    return obs.json()["id"], model.json()["id"]


@pytest.mark.asyncio
async def test_models_are_listed_for_observations_by_where_both_have_data(
    client, as_new_user: dict[str, str]
) -> None:
    obs_id, model_id = await _unfiled_sources(client)

    for_observations = await client.get(
        "/jobs/models", params={"dataset_id": obs_id}, headers=as_new_user
    )
    for_unknown = await client.get(
        "/jobs/models", params={"dataset_id": "no-such-dataset"}, headers=as_new_user
    )

    assert [model["id"] for model in for_observations.json()] == [model_id]
    assert for_unknown.json() == []
