from __future__ import annotations

from ai_almanac.server.services.forecast_models import live_forecast_compatibility
from ai_almanac.server.services.job_submission import forecast_generation_gpus


def test_all_ready_scores_gpu_free():
    assert forecast_generation_gpus({"fuxi": True, "aifs": True}) == 0


def test_cold_model_needs_a_gpu_for_anyone():
    # A model with no set yet still submits — it just requests a rollout GPU.
    assert forecast_generation_gpus({"fuxi": True, "aifs": False}) == 1


def test_stale_set_needs_a_gpu():
    assert forecast_generation_gpus({"fuxi": False}) == 1


class TestLiveForecastCompatibility:
    """A blend member can be extended live only by a registry model that runs
    on the archive's grid; the verdict feeds the blend-setup badges, the
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

    def test_unregistered_model_is_unavailable(self):
        assert live_forecast_compatibility("neuralgcm", None).status == "unavailable"


class TestResolveForecastModel:
    """A blend model name reaches its registry entry by id, normalized display
    name, or alias — the live-forecast gate, both runners, and the web badges
    all match this way."""

    def _resolve(self, name: str):
        from ai_almanac.settings import (
            get_packaged_forecast_models,
            resolve_forecast_model,
        )

        return resolve_forecast_model(get_packaged_forecast_models(), name)

    def test_matches_by_id(self):
        assert self._resolve("aifs")["id"] == "aifs"

    def test_matches_source_names_via_alias(self):
        # The registered data sources are named "AIFS Single v2" /
        # "AIFS Ensemble v2"; their blend keys must reach the aifs2 family.
        assert self._resolve("aifs_single_v2")["id"] == "aifs2"
        assert self._resolve("aifs_ensemble_v2")["id"] == "aifs2ens"

    def test_matches_by_normalized_display_name(self):
        assert self._resolve("aifs2_ens")["id"] == "aifs2ens"
        assert self._resolve("graphcast_small")["id"] == "graphcast"

    def test_unknown_name_resolves_to_none(self):
        assert self._resolve("no_such_model") is None
