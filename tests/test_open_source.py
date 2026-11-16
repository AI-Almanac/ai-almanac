"""A forecaster opens part of a registered data source in the workspace, no benchmark needed."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
import xarray as xr

from ai_almanac.server.db import get_db
from ai_almanac.server.services import boundaries, ledger, source_subset, workspaces
from ai_almanac.server.services.focus_area import FocusBox, FocusUnits
from ai_almanac.server.services.primitives import acquire, inspection
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session

ETHIOPIA = Path(__file__).parents[1] / "testdata" / "ethiopia"
SOUTH_WEST = FocusBox(lat_min=8.0, lat_max=8.5, lon_min=38.0, lon_max=38.5)


async def _register(client, kind: str) -> str:
    metadata = (
        {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"}
        if kind == "obs"
        else {"file_pattern": "{}.nc", "model_var": "tp"}
    )
    response = await client.post(
        "/data-sources",
        json={
            "kind": kind,
            "name": "Ethiopia observations" if kind == "obs" else "FuXi archive",
            "path": str(ETHIOPIA / ("obs" if kind == "obs" else "fuxi")),
            "region": "ethiopia",
            "metadata": metadata,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _someone_elses_private_source(
    owner_id: str = "someone-else", path: str = str(ETHIOPIA / "obs")
) -> str:
    source_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO data_sources (id, kind, name, path, region, metadata, "
                "location_type, status, owner_id, visibility, created_at, updated_at) "
                "VALUES (:id, 'obs', 'Private obs', :path, 'ethiopia', :metadata, 'local', "
                "'ready', :owner_id, 'private', :now, :now)"
            ),
            {
                "id": source_id,
                "owner_id": owner_id,
                "path": path,
                "metadata": json.dumps({"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"}),
                "now": now,
            },
        )
    return source_id


@pytest.fixture
async def workspace_ctx(client, as_new_user, user_id):
    workspace, session = await workspace_with_session(client, as_new_user)
    return tool_ctx(user_id, session["id"], workspace["id"], approved=False), workspace


@pytest.mark.asyncio
async def test_opening_observations_stages_only_the_asked_days_and_box_with_readable_lineage(
    client, workspace_ctx
) -> None:
    ctx, workspace = workspace_ctx
    obs = await _register(client, "obs")

    result = await acquire.open_source(
        ctx, source_id=obs, start=date(1998, 6, 1), end=date(1998, 6, 10), area=SOUTH_WEST
    )

    assert result["ok"], result
    assert result["dims"] == {"time": 10, "lat": 3, "lon": 3}
    assert (result["first"], result["last"]) == ("1998-06-01", "1998-06-10")
    [artifact] = result["workspace_artifacts"]
    assert artifact["kind"] == "dataset"
    row = await workspaces.resolve(workspace["id"], artifact["label"])
    lineage = (await workspaces.describe(workspace["id"], row))["lineage"]
    assert "acquire.open_source" in lineage
    for fragment in ("source_name=Ethiopia observations", "start=1998-06-01", "end=1998-06-10"):
        assert fragment in lineage
    assert "area_name=8°N–8.5°N, 38°E–38.5°E" in lineage


@pytest.mark.asyncio
async def test_a_model_archive_opens_by_forecast_start_date_and_keeps_every_lead_day(
    client, workspace_ctx
) -> None:
    ctx, _ = workspace_ctx
    model = await _register(client, "model")
    with xr.open_dataset(ETHIOPIA / "fuxi" / "1999.nc") as archive:
        starts = archive["time"].sel(time=slice("1999-05-01", "1999-06-30")).size
        leads = archive.sizes["day"]

    result = await acquire.open_source(
        ctx, source_id=model, start=date(1999, 5, 1), end=date(1999, 6, 30)
    )

    assert result["ok"], result
    assert result["dims"] == {"init_time": starts, "day": leads, "lat": 5, "lon": 5}


@pytest.mark.asyncio
async def test_named_areas_keep_only_the_cells_inside_their_outline(
    client, workspace_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, workspace = workspace_ctx
    obs = await _register(client, "obs")
    # A triangle over the 2x2 south-west cells that leaves out the north-east one.
    corner = [[37.9, 7.9], [38.4, 7.9], [37.9, 8.4], [37.9, 7.9]]

    async def one_zone(iso: str, level: str) -> dict:
        feature = {
            "type": "Feature",
            "properties": {"shapeName": "Corner"},
            "geometry": {"type": "Polygon", "coordinates": [corner]},
        }
        return {"geojson": {"type": "FeatureCollection", "features": [feature]}}

    monkeypatch.setattr(boundaries, "load_boundary", one_zone)

    result = await acquire.open_source(
        ctx,
        source_id=obs,
        start=date(1998, 6, 1),
        end=date(1998, 6, 1),
        area=FocusUnits(level="adm2", units=["Corner"]),
    )

    assert result["ok"], result
    assert result["dims"] == {"time": 1, "lat": 2, "lon": 2}
    staged = await workspaces.resolve(workspace["id"], result["workspace_artifacts"][0]["label"])
    with xr.open_dataset(ledger.local_path(staged)) as subset:
        has_data = subset["RAINFALL"].isel(time=0).notnull().values.tolist()
    assert has_data == [[True, True], [True, False]]


@pytest.mark.asyncio
async def test_a_subset_over_the_size_limit_is_refused_and_nothing_is_staged(
    client, workspace_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, workspace = workspace_ctx
    obs = await _register(client, "obs")
    monkeypatch.setattr(source_subset, "MAX_OPEN_SOURCE_BYTES", 1_000)

    result = await acquire.open_source(
        ctx, source_id=obs, start=date(1998, 1, 1), end=date(2000, 12, 31)
    )

    assert not result["ok"] and "limit" in result["error"]
    assert (await inspection.list_artifacts(ctx))["catalog"] == []


@pytest.mark.asyncio
async def test_another_users_private_source_cannot_be_opened(workspace_ctx) -> None:
    ctx, _ = workspace_ctx

    result = await acquire.open_source(
        ctx,
        source_id=await _someone_elses_private_source(),
        start=date(1998, 6, 1),
        end=date(1998, 6, 2),
    )

    assert not result["ok"] and "No data source" in result["error"]


OUTPUTS_BUCKET = "almanac-job-outputs-test"


@pytest.fixture
def platform_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "gcs_outputs_bucket", OUTPUTS_BUCKET)
    monkeypatch.setattr(settings, "gcs_uploads_bucket", "almanac-uploads-test")


@pytest.mark.asyncio
async def test_an_older_private_source_in_a_platform_bucket_cannot_be_opened(
    workspace_ctx, platform_buckets
) -> None:
    ctx, _ = workspace_ctx
    source_id = await _someone_elses_private_source(
        owner_id=ctx.deps.user_id, path=f"gs://{OUTPUTS_BUCKET}/some-job/output"
    )

    result = await acquire.open_source(
        ctx, source_id=source_id, start=date(2026, 6, 1), end=date(2026, 6, 2)
    )

    assert not result["ok"] and "platform's own files" in result["error"]


@pytest.mark.parametrize("scheme", ["gcs://", "gs://"])
@pytest.mark.asyncio
async def test_a_stored_source_with_a_second_scheme_cannot_be_opened(
    workspace_ctx, platform_buckets, scheme
) -> None:
    ctx, _ = workspace_ctx
    source_id = await _someone_elses_private_source(
        owner_id=ctx.deps.user_id, path=f"gs://{scheme}someones-data/obs"
    )

    result = await acquire.open_source(
        ctx, source_id=source_id, start=date(2026, 6, 1), end=date(2026, 6, 2)
    )

    assert not result["ok"] and "more than one scheme" in result["error"]


def test_only_the_platforms_own_buckets_are_refused(platform_buckets) -> None:
    from ai_almanac.server.services.data_paths import parse_data_path
    from ai_almanac.server.services.data_sources import platform_bucket_error

    def refused(raw: str) -> bool:
        return platform_bucket_error(parse_data_path(raw)) is not None

    assert refused(f"gs://{OUTPUTS_BUCKET}/a/b.nc")
    assert refused("gs://almanac-uploads-test")
    assert not refused("gs://almanac-data-ai-almanac/obs/ethiopia")
    assert not refused(str(ETHIOPIA / "obs"))


@pytest.mark.parametrize(
    ("source_path", "refusal"),
    [
        ("gs://someones-data/obs", "another bucket"),
        (f"gs://{OUTPUTS_BUCKET}/some-job", "platform's own files"),
    ],
)
def test_a_users_source_whose_listing_lands_in_a_platform_bucket_is_refused(
    monkeypatch: pytest.MonkeyPatch, platform_buckets, source_path: str, refusal: str
) -> None:
    from ai_almanac.server.services import data_sources
    from ai_almanac.server.services import storage as storage_mod

    class _EscapingStorage:
        def list_dataset_files(self, path: str, glob: str) -> list[str]:
            return [f"gs://{OUTPUTS_BUCKET}/some-job/output/forecast.nc"]

    monkeypatch.setattr(storage_mod, "get_storage", lambda: _EscapingStorage())
    source = {
        "kind": "obs",
        "name": "Someone's obs",
        "path": source_path,
        "metadata": {"obs_file_pattern": "{}.nc"},
        "owner_id": "someone",
        "visibility": "private",
    }

    with pytest.raises(ValueError, match=refusal):
        data_sources.list_source_files(source)
