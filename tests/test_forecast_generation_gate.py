from __future__ import annotations

from ai_almanac.server.services.forecast_models import live_forecast_compatibility
from ai_almanac.server.services.job_submission import forecast_generation_gpus
from ai_almanac.settings import member_forecast_model_id


def test_all_ready_scores_gpu_free():
    assert forecast_generation_gpus({"fuxi": True, "aifs": True}) == 0


def test_cold_model_needs_a_gpu_for_anyone():
    # A model with no set yet still submits — it just requests a rollout GPU.
    assert forecast_generation_gpus({"fuxi": True, "aifs": False}) == 1


def test_stale_set_needs_a_gpu():
    assert forecast_generation_gpus({"fuxi": False}) == 1


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

    def test_unlinked_archive_is_unavailable(self):
        assert live_forecast_compatibility(None, 0.25).status == "unavailable"

    def test_unregistered_model_is_unavailable(self):
        assert live_forecast_compatibility("neuralgcm", None).status == "unavailable"


class TestMemberForecastModelId:
    """Runners execute the registry model the server linked each blend member to."""

    def test_member_runs_its_linked_model(self):
        config = {"forecast_models": {"aifs_v2_india_0_25": "aifs2"}}
        assert member_forecast_model_id(config, "aifs_v2_india_0_25") == "aifs2"

    def test_job_queued_before_links_uses_member_name(self):
        assert member_forecast_model_id({}, "fuxi") == "fuxi"
