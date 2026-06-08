"""Execute a job's computational workload without owning lifecycle state."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from ai_almanac.envs.manager import run as pixi_run
from ai_almanac.paths import database_path
from ai_almanac.server.services.runner import (
    StubRunner,
    _romp_config_override_lines,
    _romp_entry_script,
)
from ai_almanac.server.services.storage import get_storage
from ai_almanac.settings import REMOTE_OBS_PROVIDERS, settings


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
    output_dir, figure_dir = storage.job_output_uri(job_id)
    from ai_almanac.server.services.runner import InProcessRunner

    env = InProcessRunner._job_env(job_id, config, output_dir, figure_dir)
    import os

    process_env = os.environ.copy()
    process_env.update(env)
    dataset_config = config.get("dataset_config", {})
    script = _romp_entry_script(
        _romp_config_override_lines(process_env),
        compute_e2s_metrics=dataset_config.get("provider") in REMOTE_OBS_PROVIDERS,
    )
    process = pixi_run(["bash", "-c", script], env=process_env)
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
    runner = StubRunner(storage)
    lat, lon = runner._resolve_grid(config)

    print("==> [STUB RUNNER] producing synthetic ROMP-shaped outputs", flush=True)
    for window in runner.WINDOWS:
        time.sleep(0.4)
        path = output_dir / f"spatial_metrics_{model_name}_{window}.nc"
        runner._write_metric_nc(path, lat, lon, model_name, window)
        print(f"    wrote {path.name}", flush=True)
    for figure_name in ("portrait", "panel_heatmap_skill"):
        path = figure_dir / f"{figure_name}_{model_name}.png"
        runner._write_placeholder_figure(path, model_name, figure_name)
        print(f"    wrote figure {path.name}", flush=True)
    print("==> [STUB RUNNER] complete", flush=True)
