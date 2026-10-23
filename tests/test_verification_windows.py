"""Verification windows: the lead-time ranges a benchmark run scores separately."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from ai_almanac.server.services import benchmark_domain, stub_outputs
from ai_almanac.server.services.benchmark_state import BenchmarkRunSpec
from ai_almanac.server.services.job_submission import parse_benchmark_settings
from ai_almanac.server.services.registry import CatalogSnapshot
from ai_almanac.server.services.romp import (
    day_bins,
    parse_verification_windows,
    render_romp_config,
    verification_settings,
)


def _rendered(romp_params: dict) -> dict:
    config = {
        "model_name": "fuxi",
        "obs_dir": "/data/obs",
        "model_dir": "/data/fuxi",
        "model_config": {"start_date": "2019-05-01", "end_date": "2024-07-31"},
        "romp_params": romp_params,
    }
    namespace: dict = {}
    exec(render_romp_config(config, Path("/tmp/out"), Path("/tmp/fig")), {}, namespace)
    return namespace


def test_jobs_without_windows_keep_romps_default_windows() -> None:
    namespace = _rendered({})

    assert namespace["verification_window_list"] == ((1, 15), (16, 30))
    assert namespace["tolerance_days_list"] == (3, 5)
    assert namespace["day_bins"] == ((1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30))
    assert namespace["max_forecast_day"] == 30


def test_custom_windows_reach_romp_with_their_tolerances_in_window_order() -> None:
    namespace = _rendered(
        {
            "max_forecast_day": 46,
            "verification_windows": [
                {"start": 31, "end": 46, "tolerance_days": 7},
                {"start": 1, "end": 7, "tolerance_days": 2},
            ],
        }
    )

    # ROMP pairs tolerances with windows by position, so both lists follow lead order.
    assert namespace["verification_window_list"] == ((1, 7), (31, 46))
    assert namespace["tolerance_days_list"] == (2, 7)
    assert namespace["max_forecast_day"] == 46


@pytest.mark.parametrize(
    ("windows", "bins"),
    [
        ([(1, 7), (8, 20)], ((1, 5), (6, 7), (8, 12), (13, 17), (18, 20))),
        # A window that starts late still gets bins from day 1 to keep the categories whole.
        ([(5, 12)], ((1, 4), (5, 9), (10, 12))),
        # Nested windows share bins rather than overlapping them.
        ([(1, 15), (1, 30)], ((1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30))),
    ],
)
def test_day_bins_partition_the_lead_range_at_every_window_edge(
    windows: list[tuple[int, int]], bins: tuple
) -> None:
    parsed = parse_verification_windows(
        [{"start": start, "end": end, "tolerance_days": 3} for start, end in windows]
    )

    assert day_bins(parsed) == bins
    for window in parsed:
        inside = [b for b in bins if window.start <= b[0] and b[1] <= window.end]
        assert (inside[0][0], inside[-1][1]) == (window.start, window.end)


@pytest.mark.parametrize(
    "windows",
    [
        [],
        [{"start": 10, "end": 5, "tolerance_days": 3}],
        [{"start": 0, "end": 5, "tolerance_days": 3}],
        [{"start": 1, "end": 15, "tolerance_days": -1}],
        [
            {"start": 1, "end": 15, "tolerance_days": 3},
            {"start": 1, "end": 15, "tolerance_days": 5},
        ],
        [{"start": 1, "end": 15}],
    ],
)
def test_malformed_windows_never_reach_romp(windows: list) -> None:
    with pytest.raises(ValidationError):
        verification_settings(windows)


def test_submission_rejects_windows_past_the_last_lead_day_read() -> None:
    windows = [{"start": 16, "end": 45, "tolerance_days": 5}]

    with pytest.raises(HTTPException) as exc:
        parse_benchmark_settings({"verification_windows": windows})
    assert exc.value.status_code == 422
    assert "max_forecast_day 30" in exc.value.detail

    settings = parse_benchmark_settings({"verification_windows": windows, "max_forecast_day": 45})
    assert settings["verification_windows"] == windows


def _window_errors(spec: BenchmarkRunSpec) -> list[str]:
    validation = benchmark_domain._validation_for_config(
        spec, CatalogSnapshot(regions=(), models=())
    )
    return [
        error
        for error in validation.errors
        if "forecast_window_days" in error or "verification_windows" in error
    ]


def test_chat_validation_measures_forecast_days_against_the_windows() -> None:
    assert _window_errors(BenchmarkRunSpec(forecast_window_days=30)) == []
    assert _window_errors(BenchmarkRunSpec(forecast_window_days=20)) == [
        "forecast_window_days must be at least 30 because the verification windows extend to day 30"
    ]
    short = {"verification_windows": [{"start": 1, "end": 10, "tolerance_days": 3}]}
    assert _window_errors(BenchmarkRunSpec(forecast_window_days=10, advanced_params=short)) == []
    bad = {"verification_windows": [{"start": 9, "end": 3, "tolerance_days": 3}]}
    assert _window_errors(BenchmarkRunSpec(advanced_params=bad))[0].startswith(
        "verification_windows:"
    )


def test_chat_submission_passes_windows_through_to_romp() -> None:
    windows = [{"start": 1, "end": 10, "tolerance_days": 3}]
    model = {
        "id": "m",
        "start_date": "2019-05-01",
        "end_date": "2024-07-31",
        "start_year_clim": 1998,
        "end_year_clim": 2024,
        "init_days": "0,3",
    }
    spec = BenchmarkRunSpec(advanced_params={"verification_windows": windows})

    assert benchmark_domain._clamp_model_params(model, spec)["verification_windows"] == windows


def test_stub_runner_emits_the_windows_and_bins_a_real_run_would(tmp_path: Path) -> None:
    windows = stub_outputs.verification_windows(
        {
            "verification_windows": [
                {"start": 1, "end": 7, "tolerance_days": 2},
                {"start": 8, "end": 20, "tolerance_days": 4},
            ]
        }
    )

    assert [(w.label, w.tolerance_days, w.bins) for w in windows] == [
        ("1-7", 2, ((1, 5), (6, 7))),
        ("8-20", 4, ((8, 12), (13, 17), (18, 20))),
    ]
    assert [w.label for w in stub_outputs.verification_windows({})] == ["1-15", "16-30"]

    _, binned = stub_outputs.write_skill_score_csvs(tmp_path, "m", "1-7", windows[0].bins)
    assert [row.split(",")[1] for row in binned.read_text().splitlines()[1:]] == ["1-5", "6-7"]
