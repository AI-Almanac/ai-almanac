"""Run the live-forecast workflow inside its managed Pixi environment.

Mirrors blend_entrypoint.py's shape. Unlike blending, forecast_pipeline is a
plain module with no Modal dependency at all, so it's imported normally here
(no stubbing needed) — see server/services/forecast_pipeline.py's docstring.
The live-scoring step still needs blending_app.py's pure scoring function
(score_live_forecast), so this reuses blend_entrypoint's existing
Modal-stubbing loader for that one step only.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

from ai_almanac.envs.blend_entrypoint import _forecast_files, _load_workflow, _netcdf_files
from ai_almanac.paths import cache_dir
from ai_almanac.server.services import forecast_pipeline
from ai_almanac.settings import get_packaged_forecast_models


def _registry_entry(model_id: str) -> dict:
    registry = get_packaged_forecast_models()
    for entry in registry.get("models") or []:
        if entry["id"] == model_id:
            return entry
    raise KeyError(f"Unknown forecast model id: {model_id!r}")


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
    params = blend_config.get("blend_params") or {}
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
        obs_bundle, forecast_bundles, model_names, params, live_year, coef_pkl=coef_pkl
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "blended_forecast_probabilities.csv").write_bytes(csv_bytes)


def run(config: dict, output_dir: Path) -> None:
    """Roll each blend model across the season-to-date (serving cached issue
    dates and rolling out only misses) and score the assembled season against
    the trained blend. The raw short-lead map deliverable was dropped (D1): it
    re-ran a rollout already contained in the season loop's latest issue date.
    """
    model_ids = config["forecast_model_ids"]
    failures: dict[str, str] = {}

    blend_config = config.get("blend_config_snapshot")
    season_model_params = config.get("season_model_params") or {}
    if not blend_config:
        raise RuntimeError("Forecast job config is missing its blend snapshot")

    print("==> Running season-long inference for blend scoring", flush=True)
    live_forecast_paths: dict[str, Path] = {}
    for model_id in model_ids:
        try:
            live_forecast_paths[model_id] = _run_season_bundle(
                model_id, config, season_model_params.get(model_id) or {}
            )
        except Exception as exc:
            failures[f"{model_id} (season)"] = str(exc)
            traceback.print_exc()

    missing = [m for m in model_ids if m not in live_forecast_paths]
    if not missing:
        try:
            _score_live(config, live_forecast_paths, output_dir)
        except Exception as exc:
            failures["blend_scoring"] = str(exc)
            traceback.print_exc()
    else:
        print(f"==> Skipping blend scoring; missing season data for {missing}", flush=True)

    if failures:
        raise RuntimeError(f"Forecast job failed for model(s) {sorted(failures)}: {failures}")
    print("==> Forecast generation complete", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    run(config, args.output_dir)


if __name__ == "__main__":
    main()
