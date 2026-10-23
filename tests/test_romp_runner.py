from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_almanac.server.services import job_workload
from ai_almanac.server.services.romp import render_romp_config, romp_safe_model_name


def _job_config() -> dict:
    return {
        "model_name": "fuxi",
        "obs_dir": "/data/observations with spaces",
        "model_dir": "/data/fuxi",
        "romp_region": "Ethiopia",
        "dataset_config": {
            "source_name": "CHIRPS Ethiopia",
            "obs_file_pattern": "{}.nc",
            "obs_var": "RAINFALL",
        },
        "model_config": {
            "file_pattern": "{}.nc",
            "model_var": "tp",
            "unit_cvt": 1000,
            "start_date": "1998-01-01",
            "end_date": "2024-12-31",
        },
        "romp_params": {
            "region": "Ethiopia",
            "start_date": "1998-01-01",
            "end_date": "2024-12-31",
            "start_year_clim": 1998,
            "end_year_clim": 2024,
            "max_forecast_day": 30,
            "init_days": "2,5",
            "wet_threshold": 25,
            "parallel": True,
        },
    }


def test_render_romp_config_propagates_job_inputs() -> None:
    rendered = render_romp_config(
        _job_config(),
        Path("/tmp/job/output"),
        Path("/tmp/job/figure"),
    )
    namespace: dict = {}

    exec(rendered, {}, namespace)

    assert namespace["obs_dir"] == "/data/observations with spaces"
    assert namespace["model_dir_list"] == ("/data/fuxi",)
    assert namespace["model_list"] == ("fuxi",)
    assert namespace["unit_cvt_list"] == (1000,)
    assert namespace["start_date"] == (1998, 1, 1)
    assert namespace["end_date"] == (2024, 12, 31)
    assert namespace["init_days"] == (2, 5)
    assert namespace["wet_threshold"] == 25
    assert namespace["plot_spatial_far_mr_mae"] is False


@pytest.mark.parametrize(
    ("display_name", "safe"),
    [
        # ROMP rejects model names with spaces.
        ("AIFS Single v2", "AIFS_Single_v2"),
        ("AIFS   Ensemble\tv2", "AIFS_Ensemble_v2"),
        ("  fuxi  ", "fuxi"),
        ("graphcast", "graphcast"),
        ("GraphCast (v2.1)", "GraphCast_v2.1"),
        # The name is written into an exec'd config, so nothing else survives.
        ('fuxi",)\n__import__("os")#', "fuxi___import___os"),
        ("()", "model"),
    ],
)
def test_romp_safe_model_name_keeps_only_name_characters(display_name: str, safe: str) -> None:
    assert romp_safe_model_name(display_name) == safe


class _Process:
    def __init__(self, exit_code: int = 0) -> None:
        self.stdout = iter(["workload output\n"])
        self.args = ["pixi", "run"]
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code


def _local_storage(output_dir: Path, figure_dir: Path):
    class Storage:
        is_local = True

        def job_output_uri(self, job_id: str) -> tuple[str, str]:
            output_dir.mkdir(parents=True, exist_ok=True)
            figure_dir.mkdir(parents=True, exist_ok=True)
            return str(output_dir), str(figure_dir)

    return Storage()


def test_benchmark_workload_hands_romp_a_config_of_the_job_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "job" / "output"
    commands: list[list[str]] = []

    def fake_pixi_run(command: list[str], env=None) -> _Process:
        commands.append(command)
        return _Process()

    monkeypatch.setattr(
        job_workload, "get_storage", lambda: _local_storage(output_dir, tmp_path / "job" / "fig")
    )
    monkeypatch.setattr(job_workload, "pixi_run", fake_pixi_run)

    job_workload._run_pixi("job-1", _job_config())

    (command,) = [command for command in commands if command[0] == "momp-run"]
    config_path = next(Path(arg) for arg in command if arg.endswith(".in"))
    namespace: dict = {}
    exec(config_path.read_text(), {}, namespace)
    assert namespace["model_list"] == ("fuxi",)
    assert namespace["obs_dir"] == "/data/observations with spaces"


@pytest.mark.parametrize("prefetch_exit_code", [0, 1])
def test_country_borders_are_fetched_once_before_romps_workers_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefetch_exit_code: int
) -> None:
    runs: list[tuple[list[str], dict]] = []

    def fake_pixi_run(command: list[str], env=None) -> _Process:
        runs.append((command, env))
        prefetch = command[-1].endswith("prefetch_boundaries.py")
        return _Process(exit_code=prefetch_exit_code if prefetch else 0)

    monkeypatch.setattr(
        job_workload, "get_storage", lambda: _local_storage(tmp_path / "out", tmp_path / "fig")
    )
    monkeypatch.setattr(job_workload, "pixi_run", fake_pixi_run)

    job_workload._run_pixi("job-1", _job_config())

    # A failed fetch is left for ROMP to report, since not every run masks by country.
    (prefetch, prefetch_env), (romp, romp_env) = runs
    assert Path(prefetch[-1]).is_file()
    assert romp[0] == "momp-run"
    # The workers read the borders from where the fetch put them, under the data dir.
    assert prefetch_env["CARTOPY_DATA_DIR"] == romp_env["CARTOPY_DATA_DIR"]
    assert Path(romp_env["CARTOPY_DATA_DIR"]) == job_workload.cache_dir() / "cartopy"


@pytest.mark.parametrize("workload", ["benchmark", "blend"])
def test_a_workload_that_exits_nonzero_fails_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workload: str
) -> None:
    def failing_run(command: list[str], env=None) -> _Process:
        return _Process(exit_code=2)

    monkeypatch.setattr(
        job_workload, "get_storage", lambda: _local_storage(tmp_path / "out", tmp_path / "fig")
    )
    monkeypatch.setattr(job_workload, "pixi_run", failing_run)
    monkeypatch.setattr(job_workload, "blending_pixi_run", failing_run)
    monkeypatch.setattr(job_workload, "blending_env_dir", lambda: tmp_path / "blend-env")

    with pytest.raises(subprocess.CalledProcessError):
        if workload == "benchmark":
            job_workload._run_pixi("job-1", _job_config())
        else:
            job_workload._run_blend("job-1", {"job_type": "blend", "model_names": ["aifs"]})


def test_blend_workload_runs_in_the_managed_blending_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "job" / "output"
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_blending_run(command: list[str], env=None) -> _Process:
        calls.append((command, env))
        return _Process()

    monkeypatch.setattr(
        job_workload, "get_storage", lambda: _local_storage(output_dir, tmp_path / "job" / "fig")
    )
    monkeypatch.setattr(job_workload, "blending_pixi_run", fake_blending_run)
    monkeypatch.setattr(job_workload, "blending_env_dir", lambda: tmp_path / "blend-env")

    job_workload._run_blend("job-1", {"job_type": "blend", "model_names": ["aifs"]})

    ((command, env),) = calls
    config_path = Path(command[command.index("--config") + 1])
    assert json.loads(config_path.read_text())["model_names"] == ["aifs"]
    assert command[command.index("--output-dir") + 1] == str(output_dir)
    assert env["ALMANAC_BLENDING_ROOT"] == str(tmp_path / "blend-env" / "onset-blending")


@pytest.mark.parametrize(
    ("year_lists", "expected"),
    [
        (
            {"years": [2012, 2014], "years_clim": [2010, 2011, 2012, 2014]},
            ((2012, 2014), (2010, 2011, 2012, 2014)),
        ),
        # Without lists ROMP evaluates the full date range.
        ({}, (None, None)),
    ],
)
def test_render_romp_config_evaluates_only_the_listed_years(
    year_lists: dict, expected: tuple
) -> None:
    config = _job_config()
    config["romp_params"] = {**config["romp_params"], **year_lists}
    namespace: dict = {}

    exec(render_romp_config(config, Path("/tmp/out"), Path("/tmp/fig")), {}, namespace)

    assert (namespace["years"], namespace["years_clim"]) == expected


def test_render_romp_config_names_the_model_files_dims_for_romp() -> None:
    config = _job_config()
    config["model_config"] = {
        **config["model_config"],
        "forecast_dims": {"init_time": "time", "step": "prediction_timedelta_daily"},
    }
    namespace: dict = {}

    exec(render_romp_config(config, Path("/tmp/out"), Path("/tmp/fig")), {}, namespace)

    assert namespace["model_dims_list"] == (
        {"init_time": "time", "step": "prediction_timedelta_daily"},
    )


def test_render_romp_config_rejects_dim_names_that_are_not_plain_names() -> None:
    from pydantic import ValidationError

    config = _job_config()
    config["model_config"] = {
        **config["model_config"],
        "forecast_dims": {"init_time": "time'); __import__('os').system('true"},
    }

    with pytest.raises(ValidationError):
        render_romp_config(config, Path("/tmp/out"), Path("/tmp/fig"))
