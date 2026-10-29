"""Run the live-forecast workflow inside its managed Pixi environments.

Split into two phases because the model rollouts and the blend scoring run in
different environments:

  - ``inference``: roll a subset of models across the season-to-date and stage
    each model's season NetCDF. Runs in a forecast model-group environment
    (see envs.manager.FORECAST_ENVIRONMENTS) — the AIFS families pin
    incompatible deps, so a job's models are grouped by `env` and this phase is
    invoked once per group.
  - ``score``: assemble the staged season files and score against the trained
    blend. Runs in the *blending* environment — scoring only needs blending's
    stack (numpy/pandas/sklearn), which conflicts with earth2studio's pins, so
    it must not live in a forecast env.

forecast_pipeline is a plain module with no Modal dependency, imported normally
here. A blend trained by onset_blending (it has a trained.blend) is applied by
it: every parameter comes from the blend, only the live files from here. Blends
trained before that are scored through blend_entrypoint's Modal-stubbing loader
for blending_app.py's score_live_forecast.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import traceback
from pathlib import Path

from ai_almanac.envs.blend_entrypoint import (
    TRAINED_BLEND,
    _forecast_files,
    _load_workflow,
    _netcdf_files,
)
from ai_almanac.paths import cache_dir
from ai_almanac.server.services import forecast_pipeline
from ai_almanac.server.services.forecast_pipeline import SeasonPlan


def _run_season_bundle(plan: SeasonPlan, member: str) -> Path:
    """Season-scoring deliverable: roll this member's model across its planned
    issue dates and write one NetCDF matching the historical `{year}.nc` schema."""
    scratch_root = Path(tempfile.mkdtemp(prefix=f"season-scratch-{member}-"))
    stage_root = Path(tempfile.mkdtemp(prefix=f"season-{member}-"))
    out_path = stage_root / f"{plan.season}.nc"
    return forecast_pipeline.generate_season_forecast_netcdf(
        plan,
        member,
        scratch_root,
        out_path,
        store=cache_dir() / "season-forecasts",
    )


def _local_blend_artifact(blend_output_uri: str, filename: str) -> bytes | None:
    """A trained blend's output file when the blend ran locally; cloud blends
    (gs://) are scored on Modal, not here."""
    if not blend_output_uri or blend_output_uri.startswith("gs://"):
        return None
    path = Path(blend_output_uri) / filename
    return path.read_bytes() if path.is_file() else None


def _local_trained_blend(blend_output_uri: str) -> Path | None:
    """The trained blend onset_blending saved, when the blend ran locally."""
    if not blend_output_uri or blend_output_uri.startswith("gs://"):
        return None
    path = Path(blend_output_uri) / TRAINED_BLEND
    return path if path.is_file() else None


def _forecast_with_trained_blend(
    path: Path, live_forecast_paths: dict[str, Path], region_id: str | None, output_dir: Path
) -> None:
    """The blend's probabilities for every live issue date. The files must
    cover what the blend was trained on (members, lead days, domain, units,
    daily aggregation); anything more is trimmed, anything less refuses."""
    from onset_blending import TrainedBlend

    blend = TrainedBlend.load(path)
    forecast = blend.forecast(dict(live_forecast_paths))
    workflow = _load_workflow()
    scores = workflow._with_live_centroids(
        workflow.LiveScores(weekly_csv=forecast.weekly.to_csv(index=False).encode("utf-8")),
        region_id,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    workflow._write_live_scores(scores, output_dir)


def _score_live(
    config: dict, live_year: int, live_forecast_paths: dict[str, Path], output_dir: Path
) -> None:
    """Score the staged live season files against the trained blend: with
    onset_blending for a blend it trained, else through blending_app.py's
    score_live_season, the same path the Modal runner takes, reading inputs
    from local files instead of GCS."""
    blend_config = config["blend_config_snapshot"]
    blend_output_uri = str(blend_config.get("blend_output_uri") or "")
    trained = _local_trained_blend(blend_output_uri)
    if trained is not None:
        print(f"==> Forecasting live season {live_year} with the trained blend", flush=True)
        _forecast_with_trained_blend(
            trained, live_forecast_paths, blend_config.get("region_id"), output_dir
        )
        return
    workflow = _load_workflow()

    print(f"==> Scoring live season {live_year} against trained blend", flush=True)
    scores = workflow.score_live_season(
        blend_config,
        {name: workflow._bundle_files([path]) for name, path in live_forecast_paths.items()},
        live_year,
        obs_bundle=workflow._bundle_files(_netcdf_files(blend_config["obs_dir"])),
        load_historical=lambda name: workflow._bundle_files(_forecast_files(blend_config, name)),
        read_artifact=lambda filename: _local_blend_artifact(blend_output_uri, filename),
        cache_dir=str(cache_dir() / "blend-intermediates"),
        resources=workflow.LOCAL_INTERMEDIATE_RESOURCES,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    workflow._write_live_scores(scores, output_dir)


def _release_gpu_memory() -> None:
    """Hand the previous model's cached CUDA blocks back before the next loads,
    so the caching allocator doesn't hold two models' worth of memory."""
    import gc

    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def run_inference(plan: SeasonPlan, members: list[str], staging_dir: Path) -> None:
    """Roll each requested member across its planned issue dates (serving stored
    dates and rolling out only misses) and stage its season file as
    `<staging_dir>/<member>.nc` for the later scoring phase."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}

    print(f"==> Season inference for {members}", flush=True)
    for member in members:
        try:
            season_path = _run_season_bundle(plan, member)
            shutil.copy(season_path, staging_dir / f"{member}.nc")
        except Exception as exc:  # noqa: BLE001
            failures[f"{member} (season)"] = str(exc)
            traceback.print_exc()
        _release_gpu_memory()

    if failures:
        raise RuntimeError(f"Forecast inference failed for {sorted(failures)}: {failures}")


def run_scoring(config: dict, plan: SeasonPlan, staging_dir: Path, output_dir: Path) -> None:
    """Score the assembled season (all staged model files) against the trained
    blend. The raw short-lead map deliverable was dropped (D1): it re-ran a
    rollout already contained in the season loop's latest issue date."""
    blend_config = config.get("blend_config_snapshot")
    if not blend_config:
        raise RuntimeError("Forecast job config is missing its blend snapshot")

    live_forecast_paths = {run.member: staging_dir / f"{run.member}.nc" for run in plan.runs}
    missing = [m for m, p in live_forecast_paths.items() if not p.is_file()]
    if missing:
        raise RuntimeError(f"Cannot score; missing staged season data for {missing}")

    _score_live(config, plan.season, live_forecast_paths, output_dir)
    print("==> Forecast generation complete", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["inference", "score"], required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--models", help="Comma-separated model ids for the inference phase.")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    plan = SeasonPlan.parse(config.get("season_plan"))

    if args.phase == "inference":
        members = [m for m in (args.models or "").split(",") if m]
        run_inference(plan, members, args.staging_dir)
    else:
        if not args.output_dir:
            parser.error("--output-dir is required for the score phase")
        run_scoring(config, plan, args.staging_dir, args.output_dir)


if __name__ == "__main__":
    main()
