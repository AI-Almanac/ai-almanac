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
here. The scoring step reuses blend_entrypoint's Modal-stubbing loader for
blending_app.py's pure score_live_forecast function only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

from ai_almanac.envs.blend_entrypoint import _forecast_files, _load_workflow, _netcdf_files
from ai_almanac.paths import cache_dir
from ai_almanac.server.services import forecast_pipeline
from ai_almanac.settings import get_packaged_forecast_models, resolve_forecast_model


def _registry_entry(model_id: str) -> dict:
    entry = resolve_forecast_model(get_packaged_forecast_models(), model_id)
    if entry is None:
        raise KeyError(f"Unknown forecast model id: {model_id!r}")
    return entry


def _run_season_bundle(model_id: str, config: dict, season_params: dict) -> Path:
    """Season-scoring deliverable: loop this model across the season-to-date
    and write one NetCDF matching the historical `{year}.nc` schema."""
    model_entry = _registry_entry(model_id)
    scratch_root = Path(tempfile.mkdtemp(prefix=f"season-scratch-{model_id}-"))
    stage_root = Path(tempfile.mkdtemp(prefix=f"season-{model_id}-"))
    year = datetime.now(UTC).year
    out_path = stage_root / f"{year}.nc"
    return forecast_pipeline.generate_season_forecast_netcdf(
        model_entry,
        config,
        season_params,
        scratch_root,
        out_path,
        cache_dir=cache_dir() / "season-forecasts",
    )


def _score_live(config: dict, live_forecast_paths: dict[str, Path], output_dir: Path) -> None:
    """Merge each model's live season file with its local historical archive
    and score against the trained blend, reusing blending_app.py's pure
    score_live_forecast (no GCS involved — that function never touches GCS,
    only the Modal-side score_live_forecast_bundle wrapper does)."""
    workflow = _load_workflow()
    blend_config = config["blend_config_snapshot"]
    params = {
        **(blend_config.get("blend_params") or {}),
        "region_id": blend_config.get("region_id"),
    }
    model_names = blend_config["model_names"]

    obs_bundle = workflow._bundle_files(_netcdf_files(blend_config["obs_dir"]))
    forecast_bundles: dict[str, bytes] = {}
    for name in model_names:
        historical_bundle = workflow._bundle_files(_forecast_files(blend_config, name))
        live_bundle = workflow._bundle_files([live_forecast_paths[name]])
        forecast_bundles[name] = workflow._merge_forecast_bundle(historical_bundle, live_bundle)

    coef_pkl = None
    blend_output_uri = str(blend_config.get("blend_output_uri") or "")
    if blend_output_uri and not blend_output_uri.startswith("gs://"):
        coef_path = Path(blend_output_uri) / workflow.FINAL_COEF_FILENAME
        if coef_path.is_file():
            print("==> Using trained blend coefficients (skipping CV retrain)", flush=True)
            coef_pkl = coef_path.read_bytes()

    live_year = datetime.now(UTC).year
    print(f"==> Scoring live season {live_year} against trained blend", flush=True)
    csv_bytes = workflow.score_live_forecast.local(
        obs_bundle,
        forecast_bundles,
        model_names,
        params,
        live_year,
        coef_pkl=coef_pkl,
        cache_dir=str(cache_dir() / "blend-intermediates"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "blended_forecast_probabilities.csv").write_bytes(csv_bytes)


def run_inference(config: dict, model_ids: list[str], staging_dir: Path) -> None:
    """Roll each requested model across the season-to-date (serving cached issue
    dates and rolling out only misses) and stage its season file as
    `<staging_dir>/<model_id>.nc` for the later scoring phase."""
    season_model_params = config.get("season_model_params") or {}
    staging_dir.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}

    print(f"==> Season inference for {model_ids}", flush=True)
    for model_id in model_ids:
        try:
            season_path = _run_season_bundle(
                model_id, config, season_model_params.get(model_id) or {}
            )
            shutil.copy(season_path, staging_dir / f"{model_id}.nc")
        except Exception as exc:  # noqa: BLE001
            failures[f"{model_id} (season)"] = str(exc)
            traceback.print_exc()

    if failures:
        raise RuntimeError(f"Forecast inference failed for {sorted(failures)}: {failures}")


def run_scoring(config: dict, staging_dir: Path, output_dir: Path) -> None:
    """Score the assembled season (all staged model files) against the trained
    blend. The raw short-lead map deliverable was dropped (D1): it re-ran a
    rollout already contained in the season loop's latest issue date."""
    blend_config = config.get("blend_config_snapshot")
    if not blend_config:
        raise RuntimeError("Forecast job config is missing its blend snapshot")

    model_ids = config["forecast_model_ids"]
    live_forecast_paths = {m: staging_dir / f"{m}.nc" for m in model_ids}
    missing = [m for m, p in live_forecast_paths.items() if not p.is_file()]
    if missing:
        raise RuntimeError(f"Cannot score; missing staged season data for {missing}")

    _score_live(config, live_forecast_paths, output_dir)
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

    if args.phase == "inference":
        model_ids = [m for m in (args.models or "").split(",") if m]
        run_inference(config, model_ids, args.staging_dir)
    else:
        if not args.output_dir:
            parser.error("--output-dir is required for the score phase")
        run_scoring(config, args.staging_dir, args.output_dir)


if __name__ == "__main__":
    main()
