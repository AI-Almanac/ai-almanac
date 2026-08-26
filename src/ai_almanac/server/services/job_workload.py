"""Execute a job's computational workload without owning lifecycle state."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import sqlalchemy as sa

from ai_almanac.envs.manager import run as pixi_run
from ai_almanac.envs.manager import run_blending as blending_pixi_run
from ai_almanac.envs.manager import run_forecast as forecast_pixi_run
from ai_almanac.paths import blending_env_dir, cache_dir
from ai_almanac.server.services import stub_outputs
from ai_almanac.server.services.bundle import build_job_env
from ai_almanac.server.services.romp import write_romp_config
from ai_almanac.server.services.storage import get_storage
from ai_almanac.server.sync_db import sync_engine
from ai_almanac.server.tables import jobs
from ai_almanac.settings import settings


def run_job_workload(job_id: str) -> None:
    with sync_engine().connect() as conn:
        row = conn.execute(sa.select(jobs.c.config_json).where(jobs.c.id == job_id)).fetchone()
    if not row:
        raise RuntimeError(f"job not found: {job_id}")
    config = json.loads(row[0] or "{}")
    if config.get("job_type") == "blend":
        _run_blend(job_id, config)
    elif config.get("job_type") == "forecast":
        _run_forecast(job_id, config)
    elif settings.runner_mode == "pixi":
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


def _run_blend(job_id: str, config: dict) -> None:
    storage = get_storage()
    output_dir_raw, _ = storage.job_output_uri(job_id)
    output_dir = Path(output_dir_raw)
    config_path = output_dir.parent / "blend-config.json"
    config = {**config, "cache_dir": str(cache_dir() / "blend-intermediates")}
    config_path.write_text(json.dumps(config))
    entrypoint = Path(__file__).parents[2] / "envs" / "blend_entrypoint.py"
    process_env = os.environ.copy()
    process_env["ALMANAC_BLENDING_ROOT"] = str(blending_env_dir() / "onset-blending")

    print(f"==> Blend config: {config_path}", flush=True)
    print("==> Starting model blending...", flush=True)
    process = blending_pixi_run(
        [
            "python",
            str(entrypoint),
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
        env=process_env,
    )
    _stream_process(process)


def _group_forecast_models_by_env(model_ids: list[str]) -> dict[str, list[str]]:
    """Group a job's models by execution environment, so each incompatible AIFS
    family is rolled out in its own subprocess (see FORECAST_ENVIRONMENTS).
    Model ids are blend model names — resolve them like the runners do, so an
    alias (e.g. aifs_single_v2 → aifs2) lands in its model's env."""
    from ai_almanac.settings import get_packaged_forecast_models, resolve_forecast_model

    registry = get_packaged_forecast_models()
    groups: dict[str, list[str]] = {}
    for model_id in model_ids:
        entry = resolve_forecast_model(registry, model_id) or {}
        groups.setdefault(entry.get("env", "base"), []).append(model_id)
    return groups


def _run_forecast(job_id: str, config: dict) -> None:
    storage = get_storage()
    output_dir_raw, _ = storage.job_output_uri(job_id)
    output_dir = Path(output_dir_raw)
    config_path = output_dir.parent / "forecast-config.json"
    config_path.write_text(json.dumps(config))
    staging_dir = output_dir.parent / "season-staging"
    entrypoint = Path(__file__).parents[2] / "envs" / "forecast_entrypoint.py"
    process_env = os.environ.copy()

    print(f"==> Forecast config: {config_path}", flush=True)

    # Inference: one subprocess per model-group environment (the AIFS families
    # can't share an env). Each stages its models' season files into staging_dir.
    groups = _group_forecast_models_by_env(config.get("forecast_model_ids") or [])
    for env_name, model_ids in groups.items():
        print(f"==> Season inference [{env_name}]: {model_ids}", flush=True)
        process = forecast_pixi_run(
            [
                "python",
                str(entrypoint),
                "--phase",
                "inference",
                "--config",
                str(config_path),
                "--staging-dir",
                str(staging_dir),
                "--models",
                ",".join(model_ids),
            ],
            env=process_env,
            environment=env_name,
        )
        _stream_process(process)

    # Scoring runs once, in the blending env — it only needs blending's stack,
    # which conflicts with earth2studio's pins and so can't live in a forecast env.
    print("==> Scoring against trained blend...", flush=True)
    score_env = os.environ.copy()
    score_env["ALMANAC_BLENDING_ROOT"] = str(blending_env_dir() / "onset-blending")
    process = blending_pixi_run(
        [
            "python",
            str(entrypoint),
            "--phase",
            "score",
            "--config",
            str(config_path),
            "--staging-dir",
            str(staging_dir),
            "--output-dir",
            str(output_dir),
        ],
        env=score_env,
    )
    _stream_process(process)
    # The rollout populated the shared trajectory store; record coverage so
    # later runs score against it GPU-free. Bookkeeping only — never fail an
    # otherwise-successful forecast over it.
    try:
        _mark_trajectory_coverage(config)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: failed to record trajectory coverage: {exc}", flush=True)


def _mark_trajectory_coverage(config: dict) -> None:
    """Record which season init dates are now cached for each model's
    `(model, init_source, season)` set, and mark the set complete.

    Runs in the server environment (sync DB) after the rollout subprocess, so
    the forecast pixi env / Modal container never needs database access. The
    init dates are recomputed from the same deterministic inputs the season
    loop used. Modal-runner jobs do not reach this path yet — their coverage
    marking is a follow-up in the completion reconciler.
    """
    from datetime import UTC, datetime

    from ai_almanac.server.services.forecast_pipeline import season_covered_dates
    from ai_almanac.server.tables import forecast_runs

    init_source = config.get("init_source", "gfs")
    season = config.get("season") or str(datetime.now(UTC).year)
    now = datetime.now(UTC).isoformat()
    covered_by_model = season_covered_dates(config)

    with sync_engine().begin() as conn:
        for name, dates in covered_by_model.items():
            covered = set(dates)
            row = (
                conn.execute(
                    sa.select(forecast_runs.c.id, forecast_runs.c.covered_init_dates).where(
                        forecast_runs.c.model_name == name,
                        forecast_runs.c.init_source == init_source,
                        forecast_runs.c.season == season,
                    )
                )
                .mappings()
                .fetchone()
            )
            if row is None:
                continue
            existing = row["covered_init_dates"] or []
            if isinstance(existing, str):
                existing = json.loads(existing)
            covered |= {str(item) for item in existing}
            conn.execute(
                sa.update(forecast_runs)
                .where(forecast_runs.c.id == row["id"])
                .values(
                    covered_init_dates=sorted(covered),
                    status="complete",
                    completed_at=now,
                )
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
    # ROMP's two output paths are mutually exclusive: a probabilistic run emits
    # skill-score CSVs and no spatial NetCDF, a deterministic run the reverse.
    probabilistic = bool((config.get("romp_params") or {}).get("probabilistic"))

    print("==> [STUB RUNNER] producing synthetic ROMP-shaped outputs", flush=True)
    for window in stub_outputs.WINDOWS:
        time.sleep(0.4)
        if probabilistic:
            for path in stub_outputs.write_skill_score_csvs(output_dir, model_name, window):
                print(f"    wrote {path.name}", flush=True)
        else:
            path = output_dir / f"spatial_metrics_{model_name}_{window}.nc"
            stub_outputs.write_metric_nc(path, lat, lon, model_name, window)
            print(f"    wrote {path.name}", flush=True)
    for figure_name in ("portrait", "panel_heatmap_skill"):
        path = figure_dir / f"{figure_name}_{model_name}.png"
        stub_outputs.write_placeholder_figure(path, model_name, figure_name)
        print(f"    wrote figure {path.name}", flush=True)
    print("==> [STUB RUNNER] complete", flush=True)
