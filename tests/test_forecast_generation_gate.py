from __future__ import annotations

import pytest

from ai_almanac.server.services.forecast_models import live_forecast_compatibility
from ai_almanac.server.services.job_submission import ForecastParams, forecast_generation_gpus


@pytest.mark.parametrize(
    ("readiness", "gpus"),
    [
        ({"fuxi": True, "aifs": True}, 0),
        # A model with no set yet still submits — it just requests a rollout GPU.
        ({"fuxi": True, "aifs": False}, 1),
        ({"fuxi": False}, 1),
    ],
)
def test_only_a_missing_or_stale_trajectory_set_needs_a_gpu(
    readiness: dict[str, bool], gpus: int
) -> None:
    assert forecast_generation_gpus(readiness) == gpus


def test_forecast_params_accept_a_known_init_source_in_any_case() -> None:
    assert ForecastParams(init_source=" IFS ").init_source == "ifs"


@pytest.mark.parametrize("init_source", ["era5", "../../other-model", ""])
def test_forecast_params_refuse_an_unknown_init_source(init_source: str) -> None:
    # It names a trajectory-store directory, so it must be a known source.
    with pytest.raises(ValueError, match="Unknown init source"):
        ForecastParams(init_source=init_source)


def test_forecast_params_check_each_models_init_source_and_member_count() -> None:
    params = ForecastParams(
        init_sources={"aifs_ensv2": " IFS "}, ensemble_members={"aifs_ensv2": 8}
    )
    assert params.init_source is None
    assert params.init_sources == {"aifs_ensv2": "ifs"}
    with pytest.raises(ValueError, match="Unknown init source"):
        ForecastParams(init_sources={"aifs_ensv2": "era5"})
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        ForecastParams(ensemble_members={"aifs_ensv2": 0})


class TestLiveForecastCompatibility:
    """A blend member can be extended live only by its linked registry model,
    and only if that model runs on the archive's grid; the verdict feeds the blend-setup badges, the
    submit-time warning, and the forecast gate alike."""

    def test_matching_grid_is_ready(self):
        verdict = live_forecast_compatibility("fuxi", 0.25)
        assert verdict.status == "ready"
        assert verdict.model_id == "fuxi"

    def test_coarser_model_is_a_grid_mismatch(self):
        verdict = live_forecast_compatibility("graphcast", 0.25)
        assert verdict.status == "grid_mismatch"
        assert "1° grid" in verdict.detail
        assert "0.25° grid" in verdict.detail

    def test_archive_without_recorded_grid_passes(self):
        assert live_forecast_compatibility("graphcast", None).status == "ready"

    @pytest.mark.parametrize(
        ("model_id", "grid_step"),
        [(None, 0.25), ("neuralgcm", None)],
        ids=["unlinked", "unregistered"],
    )
    def test_an_unlinked_or_unregistered_archive_is_unavailable(self, model_id, grid_step):
        assert live_forecast_compatibility(model_id, grid_step).status == "unavailable"


@pytest.mark.asyncio
async def test_the_model_list_says_what_each_model_can_start_from_and_its_members() -> None:
    from ai_almanac.server.services.forecast_models import load_forecast_model_registry

    models = {m["id"]: m for m in await load_forecast_model_registry()}

    assert models["aifs2ens"]["init_sources"] == ["ifs"]
    assert models["aifs2ens"]["ensemble_members"] == 26
    assert models["graphcast"]["init_sources"] == ["gfs", "ifs"]
    assert models["graphcast"]["ensemble_members"] is None
    assert models["aurora1p5"]["display_name"] == "Aurora 1.5"
    assert "nensemble" not in models["aifs2ens"]
