"""Run the blending workflow inside its managed Pixi environment."""

from __future__ import annotations

import argparse
import atexit
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
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


WORKFLOW_MODULE = "ai_almanac_blending_workflow"

# Stands on sys.path under the workflow's module name, so the workers its
# process pools spawn can re-import it by name, as they do on Modal.
_WORKFLOW_LOADER = '''"""The blending workflow under this module's name; see blend_entrypoint._load_workflow."""
import importlib.util as _util
import sys as _sys

_spec = _util.spec_from_file_location("_ai_almanac_blend_entrypoint", {entrypoint!r})
_entry = _util.module_from_spec(_spec)
_spec.loader.exec_module(_entry)
_entry._exec_workflow(_sys.modules[__name__])
'''
_loader_dir: Path | None = None


def _exec_workflow(module: ModuleType) -> None:
    """Run the workflow's source in `module`, with the local stand-in for modal."""
    path = _workflow_path()
    previous_modal = sys.modules.get("modal")
    sys.modules["modal"] = _local_modal_module()
    try:
        module.__file__ = str(path)
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    finally:
        if previous_modal is None:
            sys.modules.pop("modal", None)
        else:
            sys.modules["modal"] = previous_modal


def _workflow_loader_dir() -> Path:
    """A directory holding just the workflow's loader, on sys.path (which spawned
    workers inherit); created once per process."""
    global _loader_dir
    if _loader_dir is None:
        _loader_dir = Path(tempfile.mkdtemp(prefix="blend-workflow-"))
        loader = _WORKFLOW_LOADER.format(entrypoint=str(Path(__file__).resolve()))
        (_loader_dir / f"{WORKFLOW_MODULE}.py").write_text(loader)
        atexit.register(shutil.rmtree, _loader_dir, ignore_errors=True)
        sys.path.append(str(_loader_dir))
    return _loader_dir


def _load_workflow() -> ModuleType:
    """A fresh copy of the workflow module, importable by name.

    Its process pools spawn workers that re-import the module by name: the
    loader on sys.path lets them, where a module loaded only from a path would
    leave them unable to (and the pools to run serially).
    """
    loader = _workflow_loader_dir() / f"{WORKFLOW_MODULE}.py"
    spec = importlib.util.spec_from_file_location(WORKFLOW_MODULE, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the blending workflow")
    module = importlib.util.module_from_spec(spec)
    sys.modules[WORKFLOW_MODULE] = module
    _exec_workflow(module)
    return module


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


def _staged_dir(files: list[Path]) -> Path:
    """The files linked into a fresh directory of their own, as the builder reads them.

    Links, not copies: the builder only reads its inputs and writes elsewhere,
    and copying (as a gzipped tar held in memory) cost minutes and tens of GB
    on large archives.
    """
    stage = Path(tempfile.mkdtemp(prefix="blend-input-"))
    for path in files:
        (stage / path.name).symlink_to(path.resolve())
    return stage


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

    print("==> Building blending intermediates", flush=True)
    intermediates = workflow.build_blend_intermediates(
        _staged_dir(_netcdf_files(config["obs_dir"])),
        {name: _staged_dir(_forecast_files(config, name)) for name in model_names},
        params,
        region_id=config.get("region_id"),
        cache_dir=config.get("cache_dir"),
        resources=workflow.LOCAL_INTERMEDIATE_RESOURCES,
        model_layouts=config.get("model_layouts"),
    )
    # Read from disk, never held as bytes: on large grids the combined table
    # is the biggest object the run handles.
    combined_path = Path(intermediates["output_dir"]) / "combined_wide.pkl"

    # The same memory share the intermediates had: each CV fold worker holds
    # its own copies of the weekly table.
    train_kwargs = {
        "cores": os.cpu_count() or 1,
        "memory_budget_bytes": workflow.LOCAL_INTERMEDIATE_RESOURCES.file_memory_budget_bytes,
    }
    if params.get("formula_text"):
        train_kwargs["formula_text"] = params["formula_text"]
    years = {
        "training_years": workflow._parse_years(params.get("training_years") or "") or [],
        "cv_holdout_years": workflow._parse_years(params.get("cv_holdout_years") or "") or [],
        "true_holdout_years": workflow._parse_years(params.get("true_holdout_years") or ""),
    }
    print("==> Training blend weights", flush=True)
    training = workflow.train_blending_model_bundle.local(
        None,
        combined_wide_path=str(combined_path),
        model_names=model_names,
        return_outputs=True,
        **years,
        **train_kwargs,
    )
    if not training["manifest"].get("ok"):
        tail = "\n".join(training["manifest"].get("stderr_tail") or [])
        raise RuntimeError(f"Blend training pipeline failed:\n{tail}")

    output_dir.mkdir(parents=True, exist_ok=True)
    if training.get("outputs_tar"):
        _extract_tar(training["outputs_tar"], output_dir)
    published_combined = output_dir / "combined_wide.pkl"
    shutil.move(combined_path, published_combined)
    if config.get("train_day_level_blend"):
        _train_day_level(workflow, published_combined, model_names, years, config, output_dir)
    print("==> Model blending complete", flush=True)


def _train_day_level(workflow, combined_path, model_names, years, config, output_dir) -> None:
    """The day-level blend into output_dir, after the weekly one. As on Modal,
    where it trains in its own container, its failure leaves the weekly blend
    standing: it is logged here, not raised."""
    import traceback

    print("==> Training the day-level blend", flush=True)
    try:
        day_level = workflow.train_day_level_bundle(
            str(combined_path),
            model_names,
            cache_dir=config.get("cache_dir"),
            settings=(config.get("blend_params") or {}).get("day_level"),
            **years,
        )
    except Exception:
        traceback.print_exc()
        print("==> Day-level blend failed; the weekly blend is unaffected", flush=True)
        return
    for path in Path(day_level["output_dir"]).iterdir():
        shutil.move(path, output_dir / path.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    run(config, args.output_dir, _load_workflow())


if __name__ == "__main__":
    main()
