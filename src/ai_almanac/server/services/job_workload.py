"""Execute a job's computational workload without owning lifecycle state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import sqlalchemy as sa

from ai_almanac.envs.manager import run as pixi_run
from ai_almanac.envs.manager import run_blending as blending_pixi_run
from ai_almanac.envs.manager import run_forecast as forecast_pixi_run
from ai_almanac.paths import blending_env_dir, cache_dir
from ai_almanac.server.services import stub_outputs
from ai_almanac.server.services.bundle import build_job_env
from ai_almanac.server.services.focus_area import materialize_focus_mask
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
    config = materialize_focus_mask(config, output_dir.parent)
    config_path = write_romp_config(job_id, config, output_dir, figure_dir)
    env = build_job_env(config, output_dir_raw, figure_dir_raw)
    process_env = os.environ.copy()
    process_env.update(env)
    # Kept under the data dir so the borders survive container rebuilds.
    process_env["CARTOPY_DATA_DIR"] = str(cache_dir() / "cartopy")

    print(f"==> Benchmark config: {config_path}", flush=True)
    _prefetch_boundaries(process_env)
    print("==> Starting onset scoring...", flush=True)
    process = pixi_run(["momp-run", "-p", str(config_path)], env=process_env)
    _stream_process(process)

    if config.get("compute_e2s_metrics"):
        print("==> Starting rainfall verification metrics...", flush=True)
        e2s_script = Path(__file__).with_name("e2s.py")
        e2s_process = pixi_run(["python", str(e2s_script)], env=process_env)
        try:
            _stream_process(e2s_process)
        except subprocess.CalledProcessError as exc:
            print(
                f"WARNING: Rainfall verification metrics exited with code {exc.returncode}; "
                "Onset scores are still available.",
                flush=True,
            )


def _prefetch_boundaries(process_env: dict) -> None:
    """Fetch the country borders and land shapes once, so ROMP's parallel workers
    don't race to download them.

    A failure is left for ROMP to report: runs that don't mask by country or land
    never need them.
    """
    script = Path(__file__).parents[2] / "envs" / "prefetch_boundaries.py"
    try:
        _stream_process(pixi_run(["python", str(script)], env=process_env))
    except subprocess.CalledProcessError as exc:
        print(
            f"WARNING: Could not download country borders (exit code {exc.returncode}); "
            "continuing.",
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


def _group_forecast_models_by_env(config: dict) -> dict[str, list[str]]:
    """Group a job's blend members by execution environment, so each
    incompatible AIFS family is rolled out in its own subprocess (see
    FORECAST_ENVIRONMENTS). Members run the registry model their plan names."""
    from ai_almanac.server.services.forecast_pipeline import SeasonPlan

    groups: dict[str, list[str]] = {}
    for run in SeasonPlan.parse(config.get("season_plan")).runs:
        groups.setdefault(run.model.env, []).append(run.member)
    return groups


def _init_conditions_dir(config: dict) -> Path:
    """Where a season's rollouts keep their initial conditions."""
    from ai_almanac.server.services.forecast_pipeline import SeasonPlan

    return cache_dir() / "init-conditions" / str(SeasonPlan.parse(config.get("season_plan")).season)


def _package_path(directory: Path) -> Path:
    """A directory holding just this ai_almanac package, for a workload env's
    PYTHONPATH.

    forecast_entrypoint imports ai_almanac (forecast_pipeline, blend_entrypoint),
    which the forecast and blending envs don't install. The package's own
    parent won't do: installed, that is the server's site-packages, whose numpy
    and the rest, built for the server's Python, would shadow the env's own.
    """
    directory.mkdir(parents=True, exist_ok=True)
    link = directory / "ai_almanac"
    if not link.exists():
        link.symlink_to(Path(__file__).parents[2], target_is_directory=True)
    return directory


def _run_forecast(job_id: str, config: dict) -> None:
    storage = get_storage()
    output_dir_raw, _ = storage.job_output_uri(job_id)
    output_dir = Path(output_dir_raw)
    config_path = output_dir.parent / "forecast-config.json"
    config_path.write_text(json.dumps(config))
    staging_dir = output_dir.parent / "season-staging"
    entrypoint = Path(__file__).parents[2] / "envs" / "forecast_entrypoint.py"
    process_env = os.environ.copy()
    package_root = str(_package_path(output_dir.parent / "package-path"))
    process_env["PYTHONPATH"] = (
        f"{package_root}{os.pathsep}{process_env['PYTHONPATH']}"
        if process_env.get("PYTHONPATH")
        else package_root
    )
    # Let the caching allocator return freed blocks instead of fragmenting;
    # on unified-memory hosts the chat model wakes into what is left over.
    # Matches the Modal inference images (modal/forecasts_app.py).
    process_env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    # JAX (GraphCast, GenCast) otherwise reserves 75% of GPU memory up front:
    # on a DGX Spark that is ~95 GB of the 128 GB the whole machine shares.
    process_env.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")
    # Compiled kernels (aifs_speedups' compiled blocks, Triton's own, the JAX
    # models' XLA programs) kept between jobs, so only the first compiles them.
    process_env.setdefault("TORCHINDUCTOR_CACHE_DIR", str(cache_dir() / "torchinductor"))
    process_env.setdefault("TRITON_CACHE_DIR", str(cache_dir() / "triton"))
    process_env.setdefault("JAX_COMPILATION_CACHE_DIR", str(cache_dir() / "jax"))

    print(f"==> Forecast config: {config_path}", flush=True)

    # Inference: one subprocess per model-group environment (the AIFS families
    # can't share an env). Each stages its models' season files into staging_dir.
    # The initial conditions they download are kept for the season, so models
    # that read the same fields, and a retry after a failure, download them once.
    init_conditions = _init_conditions_dir(config)
    inference_env = {**process_env, "EARTH2STUDIO_DATA_CACHE": str(init_conditions)}
    groups = _group_forecast_models_by_env(config)
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
            env=inference_env,
            environment=env_name,
        )
        _stream_process(process)
    # Every model's season is in the trajectory store now, so its initial
    # conditions are never read again.
    shutil.rmtree(init_conditions, ignore_errors=True)

    # Scoring runs once, in the blending env — it only needs blending's stack,
    # which conflicts with earth2studio's pins and so can't live in a forecast env.
    print("==> Scoring against trained blend...", flush=True)
    score_env = process_env.copy()
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
    the forecast pixi env never needs database access. The dates are the job's
    season plan, the same ones the rollout covered. Modal-runner jobs are marked
    by the completion reconciler (trajectory_sets.mark_coverage_from_config).
    """
    from datetime import UTC, datetime

    from ai_almanac.server.services.forecast_pipeline import SeasonPlan
    from ai_almanac.server.tables import forecast_runs

    plan = SeasonPlan.parse(config.get("season_plan"))
    season = str(plan.season)
    now = datetime.now(UTC).isoformat()
    covered_by_model = plan.covered_dates()

    with sync_engine().begin() as conn:
        for name, dates in covered_by_model.items():
            covered = set(dates)
            row = (
                conn.execute(
                    sa.select(forecast_runs.c.id, forecast_runs.c.covered_init_dates).where(
                        forecast_runs.c.model_name == name,
                        forecast_runs.c.init_source == plan.run(name).init_source,
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
    romp_params = config.get("romp_params") or {}
    probabilistic = bool(romp_params.get("probabilistic"))

    print("==> [STUB RUNNER] producing synthetic ROMP-shaped outputs", flush=True)
    for window in stub_outputs.verification_windows(romp_params):
        time.sleep(0.4)
        if probabilistic:
            for path in stub_outputs.write_skill_score_csvs(
                output_dir, model_name, window.label, window.bins
            ):
                print(f"    wrote {path.name}", flush=True)
        else:
            path = output_dir / f"spatial_metrics_{model_name}_{window.label}.nc"
            stub_outputs.write_metric_nc(
                path, lat, lon, model_name, window.label, window.tolerance_days
            )
            print(f"    wrote {path.name}", flush=True)
    for figure_name in ("portrait", "panel_heatmap_skill"):
        path = figure_dir / f"{figure_name}_{model_name}.png"
        stub_outputs.write_placeholder_figure(path, model_name, figure_name)
        print(f"    wrote figure {path.name}", flush=True)
    print("==> [STUB RUNNER] complete", flush=True)
