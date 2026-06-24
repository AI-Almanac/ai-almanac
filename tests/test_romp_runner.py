from __future__ import annotations

import subprocess
from pathlib import Path

from ai_almanac.server.services import job_workload
from ai_almanac.server.services.romp import render_romp_config


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
