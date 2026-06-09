"""Execute a job's computational workload without owning lifecycle state."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from ai_almanac.envs.manager import run as pixi_run
from ai_almanac.paths import database_path
from ai_almanac.server.services import stub_outputs
from ai_almanac.server.services.bundle import build_job_env
from ai_almanac.server.services.romp import write_romp_config
from ai_almanac.server.services.storage import get_storage
from ai_almanac.settings import settings


def run_job_workload(job_id: str) -> None:
    with sqlite3.connect(database_path()) as conn:
        row = conn.execute(
            "SELECT config_json FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        raise RuntimeError(f"job not found: {job_id}")
    config = json.loads(row[0] or "{}")
    if settings.runner_mode == "pixi":
        _run_pixi(job_id, config)
    else:
        _run_stub(job_id, config)


def _run_pixi(job_id: str, config: dict) -> None:
    storage = get_storage()
    output_dir_raw, figure_dir_raw = storage.job_output_uri(job_id)
    output_dir = Path(output_dir_raw)
    figure_dir = Path(figure_dir_raw)
    config_path = write_romp_config(job_id, config, output_dir, figure_dir)
    env = build_job_env(config, output_dir_raw, figure_dir_raw)
    process_env = os.environ.copy()
    process_env.update(env)

    print(f"==> ROMP config: {config_path}", flush=True)
    print("==> Starting ROMP...", flush=True)
    process = pixi_run(["momp-run", "-p", str(config_path)], env=process_env)
    _stream_process(process)

    if config.get("compute_e2s_metrics"):
        print("==> Starting Earth2Studio metrics...", flush=True)
        e2s_script = Path(__file__).with_name("e2s.py")
        e2s_process = pixi_run(["python", str(e2s_script)], env=process_env)
        try:
            _stream_process(e2s_process)
        except subprocess.CalledProcessError as exc:
            print(
                f"WARNING: Earth2Studio metrics exited with code {exc.returncode}; "
                "ROMP outputs are still available.",
                flush=True,
            )


def _stream_process(process: subprocess.Popen) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
    exit_code = process.wait()
    if exit_code:
        raise subprocess.CalledProcessError(exit_code, process.args)


def _run_stub(job_id: str, config: dict) -> None:
    import time

    storage = get_storage()
    output_dir_raw, figure_dir_raw = storage.job_output_uri(job_id)
    output_dir = Path(output_dir_raw)
    figure_dir = Path(figure_dir_raw)
    model_name = config.get("model_name", "model")
    lat, lon = stub_outputs.resolve_grid(config)

    print("==> [STUB RUNNER] producing synthetic ROMP-shaped outputs", flush=True)
    for window in stub_outputs.WINDOWS:
        time.sleep(0.4)
        path = output_dir / f"spatial_metrics_{model_name}_{window}.nc"
        stub_outputs.write_metric_nc(path, lat, lon, model_name, window)
        print(f"    wrote {path.name}", flush=True)
    for figure_name in ("portrait", "panel_heatmap_skill"):
        path = figure_dir / f"{figure_name}_{model_name}.png"
        stub_outputs.write_placeholder_figure(path, model_name, figure_name)
        print(f"    wrote figure {path.name}", flush=True)
    print("==> [STUB RUNNER] complete", flush=True)
