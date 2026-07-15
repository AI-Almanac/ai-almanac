from __future__ import annotations

from ai_almanac.server.services.job_submission import decide_forecast_generation


def test_all_ready_scores_gpu_free_for_anyone():
    readiness = {"fuxi": (True, True), "aifs": (True, True)}
    gate = decide_forecast_generation(readiness, is_admin=False)
    assert gate.allowed is True
    assert gate.gpus == 0
    assert gate.cold_models == []


def test_cold_model_needs_admin():
    readiness = {"fuxi": (True, True), "aifs": (False, False)}  # aifs has no set yet

    blocked = decide_forecast_generation(readiness, is_admin=False)
    assert blocked.allowed is False
    assert blocked.cold_models == ["aifs"]

    allowed = decide_forecast_generation(readiness, is_admin=True)
    assert allowed.allowed is True
    assert allowed.gpus == 1


def test_stale_set_gap_fill_is_open_to_any_user():
    # Set exists but is not fully covered → bounded incremental update (D5),
    # allowed without admin and no cold models to report.
    readiness = {"fuxi": (True, False)}
    gate = decide_forecast_generation(readiness, is_admin=False)
    assert gate.allowed is True
    assert gate.gpus == 1
    assert gate.cold_models == []
