from __future__ import annotations

from ai_almanac.server.services.job_submission import forecast_generation_gpus


def test_all_ready_scores_gpu_free():
    assert forecast_generation_gpus({"fuxi": True, "aifs": True}) == 0


def test_cold_model_needs_a_gpu_for_anyone():
    # A model with no set yet still submits — it just requests a rollout GPU.
    assert forecast_generation_gpus({"fuxi": True, "aifs": False}) == 1


def test_stale_set_needs_a_gpu():
    assert forecast_generation_gpus({"fuxi": False}) == 1
