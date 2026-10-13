from __future__ import annotations

import pytest

from ai_almanac.server.services.forecast_models import live_forecast_compatibility
from ai_almanac.server.services.job_submission import forecast_generation_gpus
from ai_almanac.settings import member_forecast_model_id


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


@pytest.mark.parametrize(
    ("config", "member", "expected"),
    [
        ({"forecast_models": {"aifs_v2_india_0_25": "aifs2"}}, "aifs_v2_india_0_25", "aifs2"),
        # A job queued before members were linked runs the member's own name.
        ({}, "fuxi", "fuxi"),
    ],
)
def test_runners_execute_the_model_each_member_is_linked_to(
    config: dict, member: str, expected: str
) -> None:
    assert member_forecast_model_id(config, member) == expected
