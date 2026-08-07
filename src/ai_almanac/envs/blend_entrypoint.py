"""Run the blending workflow inside its managed Pixi environment."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace


class _LocalFunction:
    def __init__(self, function):
        self._function = function

    def local(self, *args, **kwargs):
        return self._function(*args, **kwargs)


class _LocalApp:
    def __init__(self, name: str):
        self.name = name

    def function(self, **_options):
        return lambda function: _LocalFunction(function)

    def local_entrypoint(self):
        return lambda function: function


class _NoopBuilder:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: self


def _local_modal_module() -> ModuleType:
    module = ModuleType("modal")
    module.App = _LocalApp
    module.Image = SimpleNamespace(debian_slim=lambda **_kwargs: _NoopBuilder())
    module.Secret = SimpleNamespace(from_name=lambda _name: object())
    return module


def _workflow_path() -> Path:
    packaged = Path(__file__).with_name("blending_app.py")
    if packaged.exists():
        return packaged
    source_checkout = Path(__file__).parents[3] / "modal" / "blending_app.py"
    if source_checkout.exists():
        return source_checkout
    raise FileNotFoundError("The bundled blending workflow could not be found")


def _load_workflow() -> ModuleType:
    previous_modal = sys.modules.get("modal")
    sys.modules["modal"] = _local_modal_module()
    try:
        spec = importlib.util.spec_from_file_location(
            "ai_almanac_blending_workflow", _workflow_path()
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load the blending workflow")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_modal is None:
            sys.modules.pop("modal", None)
        else:
            sys.modules["modal"] = previous_modal


def _netcdf_files(directory: str) -> list[Path]:
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"NetCDF input directory does not exist: {root}")
    files = sorted(root.glob("*.nc"))
    if not files:
        raise FileNotFoundError(f"No .nc files found in {root}")
    return files


def _forecast_files(config: dict, model_name: str) -> list[Path]:
    values = (config.get("model_files") or {}).get(model_name) or []
    files = [Path(value).expanduser() for value in values]
    missing = [str(path) for path in files if not path.is_file()]
    if not files:
        raise ValueError(f"No forecast files configured for model {model_name!r}")
    if missing:
        raise FileNotFoundError("Forecast files do not exist: " + ", ".join(missing))
    return files


def _extract_tar(data: bytes, output_dir: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        root = output_dir.resolve()
        for member in archive.getmembers():
            target = (output_dir / member.name).resolve()
            if target != root and not target.is_relative_to(root):
                raise ValueError(f"Unsafe path in blending artifacts: {member.name}")
        archive.extractall(output_dir, filter="data")


def run(config: dict, output_dir: Path, workflow: ModuleType) -> None:
    source_marker = workflow.BLENDING_ROOT / "python" / "prepare_data" / "nc_utils.py"
    if not source_marker.is_file():
        raise RuntimeError(
            "The blending source checkout is incomplete. Run `ai-almanac env prepare` "
            f"and verify that {source_marker} exists."
        )
    params = config.get("blend_params") or {}
    model_names = config.get("model_names") or []
    if not model_names:
        raise ValueError("Blend config has no models")

    obs_bundle = workflow._bundle_files(_netcdf_files(config["obs_dir"]))
    forecast_bundles = {
        name: workflow._bundle_files(_forecast_files(config, name)) for name in model_names
    }
    prep_kwargs = {
        key: params[key]
        for key in ("threshold_mm", "cutoff_month_day", "mok_month_day")
        if params.get(key) is not None
    }
    if config.get("region_id"):
        prep_kwargs["region_id"] = config["region_id"]

    print("==> Building blending intermediates", flush=True)
    intermediates = workflow.build_lat_lon_intermediates_bundle.local(
        obs_bundle,
        forecast_bundles,
        return_outputs=True,
        cache_dir=config.get("cache_dir"),
        **prep_kwargs,
    )
    combined = workflow._read_tar_member_bytes(intermediates["outputs_tar"], "combined_wide.pkl")

    train_kwargs = {}
    if params.get("formula_text"):
        train_kwargs["formula_text"] = params["formula_text"]
    print("==> Training blend weights", flush=True)
    training = workflow.train_blending_model_bundle.local(
        combined,
        model_names=model_names,
        training_years=workflow._parse_years(params.get("training_years") or "") or [],
        cv_holdout_years=workflow._parse_years(params.get("cv_holdout_years") or "") or [],
        true_holdout_years=workflow._parse_years(params.get("true_holdout_years") or ""),
        return_outputs=True,
        **train_kwargs,
    )
    if not training["manifest"].get("ok"):
        tail = "\n".join(training["manifest"].get("stderr_tail") or [])
        raise RuntimeError(f"Blend training pipeline failed:\n{tail}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "combined_wide.pkl").write_bytes(combined)
    if training.get("outputs_tar"):
        _extract_tar(training["outputs_tar"], output_dir)
    print("==> Model blending complete", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    run(config, args.output_dir, _load_workflow())


if __name__ == "__main__":
    main()
