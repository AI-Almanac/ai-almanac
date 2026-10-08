"""create_blend_for_user — blend job assembly and dispatch.

Seeds gs:// obs/model data sources directly (bypassing path validation), stubs
the job runner so no Modal call happens, and asserts the persisted job carries
the blend discriminator and routing config the ModalRunner relies on.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from ai_almanac.server.services import job_submission
from ai_almanac.server.services.execution import RunnerHandle
from ai_almanac.server.services.job_submission import (
    BlendCreate,
    BlendParams,
    _blend_model_key,
    create_blend_for_user,
)
from ai_almanac.server.tables import jobs


class _FakeRunner:
    name = "modal"

    async def submit(self, request):  # noqa: ANN001 - test double
        return RunnerHandle(runner="modal", external_id="fc-test")


async def _seed_source(
    kind: str,
    name: str,
    path: str,
    years: tuple[int, int] | None = None,
    grid_step: float | None = None,
    extra_metadata: dict | None = None,
) -> str:
    from ai_almanac.server.db import get_db

    source_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    metadata = {"start_year": years[0], "end_year": years[1]} if years else {}
    if grid_step is not None:
        metadata["grid_step_deg"] = grid_step
    metadata.update(extra_metadata or {})
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, 'india', :metadata, 'gcs', 'ready', "
                "NULL, :now, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": path,
                "metadata": json.dumps(metadata),
                "now": now,
            },
        )
    return source_id


@pytest.mark.parametrize(
    ("name", "expected"),
    [("GenCast", "gencast"), ("AIFS v2", "aifs_v2"), ("a/b c", "a_b_c")],
)
def test_blend_model_key_slugifies(name: str, expected: str) -> None:
    assert _blend_model_key(name) == expected


@pytest_asyncio.fixture
async def _stub_runner(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(job_submission, "get_job_runner", lambda: _FakeRunner())


@pytest.mark.asyncio
async def test_create_blend_persists_blend_routing_config(
    client, user_id: str, _stub_runner
) -> None:
    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")
    gencast_id = await _seed_source("model", "GenCast", "gs://data/models/gencast")
    aifs_id = await _seed_source("model", "AIFS", "gs://data/models/aifs")

    out = await create_blend_for_user(
        BlendCreate(
            name="my blend",
            obs_dataset_id=obs_id,
            model_ids=[gencast_id, aifs_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )

    assert out.name == "my blend"
    assert out.model_names == ["gencast", "aifs"]

    async with get_db() as conn:
        row = (await conn.execute(sa.select(jobs).where(jobs.c.id == out.id))).mappings().fetchone()
    assert row["job_type"] == "blend"
    assert row["status"] == "running"  # remote runner is live once spawned
    assert row["runner"] == "modal"
    config = json.loads(row["config_json"])
    assert config["modal_function"] == "run_blend"
    assert config["modal_app"]  # blend app name travels on the job
    assert config["model_names"] == ["gencast", "aifs"]
    # training 2019:2024 ∪ cv 2024 → the staging years, resolved server-side.
    assert config["forecast_years"] == [2019, 2020, 2021, 2022, 2023, 2024]
    assert config["model_files"]["gencast"] == [
        f"gs://data/models/gencast/{year}.nc" for year in range(2019, 2025)
    ]
    assert config["model_files"]["aifs"] == [
        f"gs://data/models/aifs/{year}.nc" for year in range(2019, 2025)
    ]
    assert config["blend_params"]["training_years"] == "2019:2024"


@pytest.mark.asyncio
async def test_blend_config_carries_each_models_registered_dims_and_units(
    client, user_id: str, _stub_runner
) -> None:
    from ai_almanac.server.db import get_db

    dims = {"init_time": "time", "step": "prediction_timedelta_daily", "lat": "lat", "lon": "lon"}
    obs_id = await _seed_source("obs", "IMD", "gs://data/obs/imd")
    aifs_id = await _seed_source(
        "model",
        "AIFS v2",
        "gs://data/models/aifs",
        extra_metadata={"forecast_dims": dims, "unit_cvt": 1000.0},
    )
    legacy_id = await _seed_source("model", "FuXi", "gs://data/models/fuxi")

    out = await create_blend_for_user(
        BlendCreate(
            name="india",
            obs_dataset_id=obs_id,
            model_ids=[aifs_id, legacy_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )

    async with get_db() as conn:
        row = (await conn.execute(sa.select(jobs).where(jobs.c.id == out.id))).mappings().fetchone()
    layouts = json.loads(row["config_json"])["model_layouts"]
    assert layouts["aifs_v2"] == {"forecast_dims": dims, "unit_cvt": 1000.0}
    assert layouts["fuxi"] == {"forecast_dims": None, "unit_cvt": 1.0}


@pytest.mark.asyncio
async def test_post_blends_rejects_a_start_date_dim_blending_cannot_decode(
    client, user_id: str, auth_headers: dict[str, str], _stub_runner
) -> None:
    obs_id = await _seed_source("obs", "IMD", "gs://data/obs/imd", years=(2000, 2024))
    model_id = await _seed_source(
        "model",
        "Renamed",
        "gs://data/models/renamed",
        years=(2000, 2024),
        extra_metadata={"forecast_dims": {"init_time": "issued", "step": "lead"}},
    )

    response = await client.post(
        "/blends",
        headers=auth_headers,
        json={
            "name": "renamed",
            "obs_dataset_id": obs_id,
            "model_ids": [model_id],
            "params": {"training_years": "2010:2020", "cv_holdout_years": "2020"},
        },
    )

    assert response.status_code == 400
    assert "Renamed names it 'issued'" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_blends_rejects_thin_climatology_coverage(
    client, user_id: str, auth_headers: dict[str, str], _stub_runner
) -> None:
    """The API must apply the same coverage rule as the UI and the chat path:
    the onset climatology needs MIN_ONSET_YEARS of observations before the
    first forecast year."""
    obs_id = await _seed_source("obs", "ERA5 thin", "gs://data/obs/thin", years=(2010, 2024))
    model_id = await _seed_source("model", "AIFS", "gs://data/models/aifs", years=(2010, 2024))

    response = await client.post(
        "/blends",
        headers=auth_headers,
        json={
            "name": "too early",
            "obs_dataset_id": obs_id,
            "model_ids": [model_id],
            # Obs start 2010 → the first estimable forecast year is 2020.
            "params": {"training_years": "2015:2024", "cv_holdout_years": "2024"},
        },
    )

    assert response.status_code == 400
    assert "Climatology needs 10 years of observations" in response.json()["detail"]


def test_grid_mismatch_names_each_model_off_the_observation_grid() -> None:
    errors = job_submission.grid_mismatch_errors(
        0.25, [("GraphCast", 2.0), ("AIFS", 0.25), ("FuXi", 1.0), ("Legacy", None)]
    )
    assert errors == [
        "The observations are on a 0.25° grid, but FuXi is on a 1° grid; "
        "GraphCast is on a 2° grid. Choose observations and models on the same grid."
    ]


def test_grid_mismatch_skips_sources_without_a_recorded_grid() -> None:
    assert job_submission.grid_mismatch_errors(None, [("GraphCast", 2.0)]) == []
    assert job_submission.grid_mismatch_errors(0.25, [("AIFS", 0.25)]) == []


@pytest.mark.asyncio
async def test_post_blends_rejects_models_on_another_grid(
    client, user_id: str, auth_headers: dict[str, str], _stub_runner
) -> None:
    obs_id = await _seed_source(
        "obs", "IMD 0.25", "gs://data/obs/imd", years=(1990, 2024), grid_step=0.25
    )
    model_id = await _seed_source(
        "model", "GraphCast", "gs://data/models/gc", years=(1990, 2024), grid_step=2.0
    )

    response = await client.post(
        "/blends",
        headers=auth_headers,
        json={
            "name": "mismatched",
            "obs_dataset_id": obs_id,
            "model_ids": [model_id],
            "params": {"training_years": "2010:2020", "cv_holdout_years": "2020"},
        },
    )

    assert response.status_code == 400
    assert "GraphCast is on a 2° grid" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_jobs_rejects_a_model_on_another_grid(
    client, user_id: str, auth_headers: dict[str, str]
) -> None:
    obs_id = await _seed_source("obs", "IMD 0.25", "gs://data/obs/imd", grid_step=0.25)
    model_id = await _seed_source("model", "GraphCast", "gs://data/models/gc", grid_step=2.0)

    response = await client.post(
        "/jobs",
        headers=auth_headers,
        json={"dataset_id": obs_id, "model_name": model_id, "params": {"region": "india"}},
    )

    assert response.status_code == 400
    assert "GraphCast is on a 2° grid" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_blends_accepts_sufficient_coverage(
    client, user_id: str, auth_headers: dict[str, str], _stub_runner
) -> None:
    obs_id = await _seed_source("obs", "ERA5 deep", "gs://data/obs/deep", years=(2010, 2024))
    model_id = await _seed_source("model", "AIFS", "gs://data/models/aifs", years=(2010, 2024))

    response = await client.post(
        "/blends",
        headers=auth_headers,
        json={
            "name": "late enough",
            "obs_dataset_id": obs_id,
            "model_ids": [model_id],
            "params": {"training_years": "2020:2024", "cv_holdout_years": "2024"},
        },
    )

    assert response.status_code == 201, response.text
    # 2020 is exactly `earliest_forecast`, and the boundary is inclusive — a
    # coverage violation would be a 400, so the 201 is what proves the rule.
    # The five-year training span this fixture is forced into (obs starts 2010,
    # so nothing before 2020 is available) does draw the small-sample guardrail,
    # which also confirms the guardrails run on the REST path and not only in
    # the chat validation path.
    warnings = response.json()["warnings"]
    assert any("small sample" in w for w in warnings), warnings
    assert not any("Climatology needs" in w for w in warnings), warnings


@pytest.mark.asyncio
async def test_create_blend_warns_when_a_member_cannot_forecast_live(
    client, user_id: str, _stub_runner
) -> None:
    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")
    ngcm_id = await _seed_source("model", "NeuralGCM", "gs://data/models/ngcm")
    aifs_id = await _seed_source("model", "AIFS", "gs://data/models/aifs")

    out = await create_blend_for_user(
        BlendCreate(
            name="history only",
            obs_dataset_id=obs_id,
            model_ids=[ngcm_id, aifs_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )

    # Asserted by content rather than by count: the same list also carries any
    # statistical guardrail warnings the config draws (here, a six-year training
    # span), and this test is about the live-forecast note specifically.
    live_forecast = next(w for w in out.warnings if "NeuralGCM" in w)
    assert "cannot be run as a live forecast" in live_forecast

    async with get_db() as conn:
        row = (await conn.execute(sa.select(jobs).where(jobs.c.id == out.id))).mappings().fetchone()
    assert json.loads(row["config_json"])["warnings"] == out.warnings


@pytest.mark.asyncio
async def test_create_forecast_rejects_blend_with_an_unforecastable_member(
    client, user_id: str, _stub_runner
) -> None:
    """Live scoring needs every member's season, so requesting only the runnable
    subset of a history-only blend must fail at submission, not after rollout."""
    from fastapi import HTTPException

    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")
    ngcm_id = await _seed_source("model", "NeuralGCM", "gs://data/models/ngcm")
    aifs_id = await _seed_source(
        "model", "AIFS", "gs://data/models/aifs", extra_metadata={"forecast_model_id": "aifs"}
    )

    blend = await create_blend_for_user(
        BlendCreate(
            name="ngcm + aifs",
            obs_dataset_id=obs_id,
            model_ids=[ngcm_id, aifs_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )
    async with get_db() as conn:
        await conn.execute(sa.update(jobs).where(jobs.c.id == blend.id).values(status="complete"))

    with pytest.raises(HTTPException) as exc:
        await job_submission.create_forecast_for_user(
            job_submission.ForecastCreate(blend_id=blend.id, forecast_model_ids=["aifs"]),
            user_id,
        )
    assert exc.value.status_code == 400
    assert "neuralgcm: This data isn't linked to a live forecast model." in exc.value.detail
    assert "aifs:" not in exc.value.detail


def test_live_forecast_blockers_follow_the_link_not_the_name() -> None:
    BlendMember = job_submission.BlendMember
    blockers = job_submission.live_forecast_blockers(
        [BlendMember("aifs", None, 0.25), BlendMember("aifs_v2_india_0_25", "aifs2", 0.25)]
    )
    assert [blocker.split(":")[0] for blocker in blockers] == ["aifs"]


def test_live_forecast_blockers_reject_grid_mismatch() -> None:
    BlendMember = job_submission.BlendMember
    blockers = job_submission.live_forecast_blockers(
        [BlendMember("graphcast_india", "graphcast", 0.25), BlendMember("fuxi", "fuxi", 0.25)]
    )
    assert len(blockers) == 1
    assert blockers[0].startswith("graphcast_india:")
    assert "1° grid" in blockers[0]


@pytest.mark.asyncio
async def test_create_blend_rejects_non_model_source(client, user_id: str, _stub_runner) -> None:
    from fastapi import HTTPException

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")

    with pytest.raises(HTTPException) as exc:
        await create_blend_for_user(
            BlendCreate(
                name="bad",
                obs_dataset_id=obs_id,
                model_ids=[obs_id],  # an obs source is not a valid model
                params=BlendParams(training_years="2024", cv_holdout_years="2024"),
            ),
            user_id,
        )
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"start_year": 1990, "end_year": 2020}, (1990, 2020)),
        ({"start_year": "1990", "end_year": "2020"}, (1990, 2020)),
        ({"start_year": "unknown", "end_year": 2020}, (None, 2020)),
        ({"start_year": {"a": 1}, "end_year": 2020}, (None, 2020)),
        ({"start_year": True, "end_year": 2020}, (None, 2020)),
        ({}, (None, None)),
    ],
)
def test_source_year_range_parses_registered_years(
    metadata: dict, expected: tuple[int | None, int | None]
) -> None:
    """Source metadata is a free-form caller-supplied dict, so a year that isn't
    one must read as unregistered rather than reaching the coverage arithmetic
    and 500ing."""
    assert job_submission.source_year_range({"metadata": metadata}) == expected


@pytest.mark.asyncio
async def test_create_blend_hides_another_users_private_obs_source(
    client, user_id: str, _stub_runner
) -> None:
    """An obs source id is not a capability: the coverage error would otherwise
    disclose a private source's registered years to anyone holding its id."""
    from fastapi import HTTPException

    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "Someone else's ERA5", "gs://data/obs/india", (1990, 2024))
    gencast_id = await _seed_source("model", "GenCast", "gs://data/models/gencast", (2000, 2024))
    async with get_db() as conn:
        await conn.execute(
            text(
                "UPDATE data_sources SET owner_id = 'another-user', visibility = 'private' "
                "WHERE id = :id"
            ),
            {"id": obs_id},
        )

    with pytest.raises(HTTPException) as exc:
        await create_blend_for_user(
            BlendCreate(
                name="peek",
                obs_dataset_id=obs_id,
                model_ids=[gencast_id],
                params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
            ),
            user_id,
        )
    assert exc.value.status_code == 404
    assert "1990" not in str(exc.value.detail)


@pytest.mark.parametrize("spec", ["0:99999999", "1:2024", "2019:99999", "-5:5"])
def test_parse_year_spec_rejects_years_outside_the_calendar(spec: str) -> None:
    """A range spec expands into a list before anything else validates it, so an
    absurd bound must fail parsing rather than allocate."""
    with pytest.raises(ValueError, match="year out of range"):
        job_submission._parse_year_spec(spec)


def test_parse_year_spec_accepts_a_normal_range() -> None:
    assert job_submission._parse_year_spec("2019:2021") == [2019, 2020, 2021]


@pytest.mark.asyncio
async def test_not_ready_shared_source_does_not_disclose_its_path(
    client, user_id: str, _stub_runner
) -> None:
    """A registration failure names the source's gs:// path, so only its owner
    sees the real reason -- a shared source just reports not-ready."""
    from fastapi import HTTPException

    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "Shared ERA5", "gs://private-bucket/obs/india")
    async with get_db() as conn:
        await conn.execute(
            text(
                "UPDATE data_sources SET owner_id = 'another-user', visibility = 'shared', "
                "status = 'invalid', validation_error = :err WHERE id = :id"
            ),
            {
                "id": obs_id,
                "err": "No files match '*.nc' under gs://private-bucket/obs/india.",
            },
        )

    with pytest.raises(HTTPException) as exc:
        await job_submission._resolve_obs_dir(obs_id, user_id)

    assert exc.value.status_code == 409
    assert "private-bucket" not in str(exc.value.detail)
    assert exc.value.detail == "Observation source is not ready"


def test_blend_coverage_rejects_forecast_years_a_source_is_missing():
    coverage = job_submission.blend_year_coverage(
        (1990, 2012),
        [(2000, 2012)],
        job_submission.source_missing_years({"missing_years": [2005, "2007", "x"]}),
    )

    errors = job_submission.blend_coverage_errors([2004, 2005, 2006, 2007], coverage)

    assert any("no data for 2005, 2007" in error for error in errors)
    assert job_submission.blend_coverage_errors([2004, 2006], coverage) == []


# --- BlendParams onset definition -------------------------------------------


def test_blend_params_accepts_legacy_mok_month_day_alias() -> None:
    # onset_blending renamed "MOK date" to "reference onset"; pre-rename clients
    # and stored payloads still say mok_month_day.
    params = job_submission.BlendParams(
        training_years="2019:2024", cv_holdout_years="2024", mok_month_day="06-05"
    )
    assert params.ref_onset_month_day == "06-05"
    dumped = params.model_dump(exclude_none=True)
    assert "mok_month_day" not in dumped
    assert dumped["ref_onset_month_day"] == "06-05"

    # The new name wins when both are present.
    params = job_submission.BlendParams(
        training_years="2019:2024",
        cv_holdout_years="2024",
        mok_month_day="06-05",
        ref_onset_month_day="06-10",
    )
    assert params.ref_onset_month_day == "06-10"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"threshold_mm": 0}, "must be positive"),
        ({"threshold_mm": -3.5}, "must be positive"),
        ({"cutoff_month_day": "2024-05-01"}, "MM-DD"),
        ({"cutoff_month_day": "13-01"}, "MM-DD"),
        ({"ref_onset_month_day": "02-30"}, "MM-DD"),
        ({"cutoff_month_day": "5-1"}, "MM-DD"),
        ({"cutoff_month_day": "02-29"}, "not Feb 29"),
        ({"threshold_mm": float("inf")}, "must be positive"),
        ({"cutoff_month_day": "06-15", "ref_onset_month_day": "06-01"}, "before the onset search"),
        # One override is checked against the other field's default (05-01 / 06-01).
        ({"ref_onset_month_day": "04-15"}, "before the onset search start 05-01"),
        ({"cutoff_month_day": "06-15"}, "Reference onset date 06-01 is before"),
    ],
)
def test_blend_params_rejects_bad_onset_definition(overrides: dict, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        job_submission.BlendParams(training_years="2019:2024", cv_holdout_years="2024", **overrides)


def test_blend_params_accepts_valid_onset_definition() -> None:
    params = job_submission.BlendParams(
        training_years="2019:2024",
        cv_holdout_years="2024",
        threshold_mm=25.5,
        cutoff_month_day="04-15",
        ref_onset_month_day="05-01",
    )
    assert params.threshold_mm == 25.5
    assert job_submission.onset_param_errors(None, None, None) == []


async def _link_forecast_model(source_id: str, forecast_model_id: str) -> None:
    from ai_almanac.server.services import data_sources

    source = await data_sources.get_source(source_id)
    metadata = {**source["metadata"], "forecast_model_id": forecast_model_id}
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        await conn.execute(
            text("UPDATE data_sources SET metadata = :metadata WHERE id = :id"),
            {"metadata": json.dumps(metadata), "id": source_id},
        )


@pytest.mark.asyncio
async def test_linking_member_data_after_training_makes_the_blend_forecastable(
    client, user_id: str, auth_headers: dict[str, str], _stub_runner
) -> None:
    """Archive names say nothing about the live model; the link on each member's
    data source decides, so a blend trained before linking forecasts once linked."""
    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "IMD India", "gs://data/obs/india", grid_step=0.25)
    aifs_id = await _seed_source(
        "model", "AIFS v2 India 0.25", "gs://data/models/aifs2", grid_step=0.25
    )
    graphcast_id = await _seed_source(
        "model", "GraphCast India 0.25", "gs://data/models/graphcast", grid_step=0.25
    )
    blend = await create_blend_for_user(
        BlendCreate(
            name="india",
            obs_dataset_id=obs_id,
            model_ids=[aifs_id, graphcast_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )
    async with get_db() as conn:
        await conn.execute(sa.update(jobs).where(jobs.c.id == blend.id).values(status="complete"))
    assert blend.forecast_models == {}

    await _link_forecast_model(aifs_id, "aifs2")
    await _link_forecast_model(graphcast_id, "graphcast_operational")

    listed = (await client.get("/blends", headers=auth_headers)).json()
    linked = {"aifs_v2_india_0_25": "aifs2", "graphcast_india_0_25": "graphcast_operational"}
    assert next(b for b in listed if b["id"] == blend.id)["forecast_models"] == linked

    forecast = await job_submission.create_forecast_for_user(
        job_submission.ForecastCreate(blend_id=blend.id), user_id
    )
    async with get_db() as conn:
        row = (
            (await conn.execute(sa.select(jobs).where(jobs.c.id == forecast.id)))
            .mappings()
            .fetchone()
        )
    assert json.loads(row["config_json"])["forecast_models"] == linked


@pytest.mark.parametrize("link", [["aifs"], "", "  "])
def test_malformed_link_on_uninspected_source_counts_as_unlinked(link) -> None:
    cfg = {"model_names": ["remote"], "model_source_ids": ["s1"]}
    sources = {"s1": {"metadata": {"forecast_model_id": link}}}
    assert job_submission.blend_forecast_models(cfg, sources) == {}
