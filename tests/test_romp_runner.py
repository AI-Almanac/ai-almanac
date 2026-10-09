from __future__ import annotations

import subprocess
from pathlib import Path

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


def test_romp_safe_model_name_collapses_whitespace() -> None:
    # ROMP rejects model names with spaces; display names like "AIFS Single v2" must be sanitized.
    assert romp_safe_model_name("AIFS Single v2") == "AIFS_Single_v2"
    assert romp_safe_model_name("AIFS   Ensemble\tv2") == "AIFS_Ensemble_v2"
    assert romp_safe_model_name("  fuxi  ") == "fuxi"
    assert romp_safe_model_name("graphcast") == "graphcast"


def test_pixi_workload_writes_config_and_invokes_momp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_dir = tmp_path / "job" / "output"
    figure_dir = tmp_path / "job" / "figure"
    output_dir.mkdir(parents=True)
    figure_dir.mkdir()
    commands: list[list[str]] = []

    class Storage:
        def job_output_uri(self, job_id: str) -> tuple[str, str]:
            return str(output_dir), str(figure_dir)

    class Process:
        stdout = iter(["ROMP output\n"])
        args = ["pixi", "run"]

        def wait(self) -> int:
            return 0

    def fake_pixi_run(command: list[str], env=None) -> subprocess.Popen:
        commands.append(command)
        return Process()

    monkeypatch.setattr(job_workload, "get_storage", lambda: Storage())
    monkeypatch.setattr(job_workload, "pixi_run", fake_pixi_run)

    job_workload._run_pixi("job-1", _job_config())

    config_path = tmp_path / "job" / "romp-config.in"
    assert config_path.exists()
    assert commands == [["momp-run", "-p", str(config_path)]]


def test_blend_workload_invokes_managed_blending_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_dir = tmp_path / "job" / "output"
    figure_dir = tmp_path / "job" / "figure"
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []

    class Storage:
        is_local = True

        def job_output_uri(self, job_id: str) -> tuple[str, str]:
            output_dir.mkdir(parents=True, exist_ok=True)
            figure_dir.mkdir(parents=True, exist_ok=True)
            return str(output_dir), str(figure_dir)

    class Process:
        stdout = iter(["blend output\n"])
        args = ["pixi", "run"]

        def wait(self) -> int:
            return 0

    def fake_blending_run(command: list[str], env=None) -> subprocess.Popen:
        commands.append(command)
        environments.append(env)
        return Process()

    monkeypatch.setattr(job_workload, "get_storage", lambda: Storage())
    monkeypatch.setattr(job_workload, "blending_pixi_run", fake_blending_run)
    monkeypatch.setattr(job_workload, "blending_env_dir", lambda: tmp_path / "blend-env")

    config = {"job_type": "blend", "model_names": ["aifs"]}
    job_workload._run_blend("job-1", config)

    config_path = tmp_path / "job" / "blend-config.json"
    assert config_path.exists()
    assert commands[0][0] == "python"
    assert commands[0][2:] == [
        "--config",
        str(config_path),
        "--output-dir",
        str(output_dir),
    ]
    assert environments[0]["ALMANAC_BLENDING_ROOT"] == str(
        tmp_path / "blend-env" / "onset-blending"
    )


def test_romp_safe_model_name_keeps_only_name_characters() -> None:
    assert romp_safe_model_name('fuxi",)\n__import__("os")#') == "fuxi___import___os"
    assert romp_safe_model_name("GraphCast (v2.1)") == "GraphCast_v2.1"
    assert romp_safe_model_name("()") == "model"


def test_render_romp_config_evaluates_only_the_listed_years() -> None:
    config = _job_config()
    config["romp_params"] = {
        **config["romp_params"],
        "years": [2012, 2014],
        "years_clim": [2010, 2011, 2012, 2014],
    }
    namespace: dict = {}

    exec(render_romp_config(config, Path("/tmp/out"), Path("/tmp/fig")), {}, namespace)

    assert namespace["years"] == (2012, 2014)
    assert namespace["years_clim"] == (2010, 2011, 2012, 2014)


def test_render_romp_config_without_year_lists_lets_romp_use_the_full_range() -> None:
    namespace: dict = {}

    exec(render_romp_config(_job_config(), Path("/tmp/out"), Path("/tmp/fig")), {}, namespace)

    assert namespace["years"] is None
    assert namespace["years_clim"] is None


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
    import pytest
    from pydantic import ValidationError

    config = _job_config()
    config["model_config"] = {
        **config["model_config"],
        "forecast_dims": {"init_time": "time'); __import__('os').system('true"},
    }

    with pytest.raises(ValidationError):
        render_romp_config(config, Path("/tmp/out"), Path("/tmp/fig"))
