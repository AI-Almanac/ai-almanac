"""Pixi-managed scientific workload environments.

The benchmark environment carries ROMP and its scientific dependencies
separately from the ai-almanac web server's own environment. This keeps
`pip install ai-almanac` small and isolates FastAPI from the benchmark stack.

The env lives at `$AI_ALMANAC_DATA_DIR/benchmark-env/` and is prepared on
first use via `ai-almanac env prepare`.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

from ai_almanac.paths import benchmark_env_dir, blending_env_dir, forecast_env_dir

BLENDING_REPO_URL = "https://github.com/hholb/onset_blending-adm3.git"
# Keep in sync with modal/blending_app.py DEFAULT_REPO_REF; tests/test_blending_pins.py
# enforces it. See docs/onset-blending-haiyang-integration.md for the pin history.
BLENDING_REPO_REF = "2a59cec0680dcfb575104fa03b59ee64dc110f82"
BLENDING_SOURCE_MARKER = Path("python/prepare_data/nc_utils.py")

# Platforms forecast.pixi.toml declares — earth2studio's GPU extras (e.g.
# fuxi's onnxruntime-gpu) ship no macOS/Windows wheels at all, and
# forecast_pipeline.load_model() hard-requires CUDA regardless, so there's no
# point solving (or running) this env anywhere else.
_FORECAST_PLATFORMS = ("linux-64", "linux-aarch64")

# The forecast manifest defines one pixi environment per model group — the AIFS
# families pin incompatible anemoi-models versions and cannot share a solve (see
# forecast.pixi.toml). A model's `env` field in forecast_models.yaml selects one.
FORECAST_ENVIRONMENTS = ("base", "aifs2", "aifs2ens")


def _current_pixi_platform() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        return "linux-aarch64" if machine in ("aarch64", "arm64") else "linux-64"
    if system == "Darwin":
        return "osx-arm64" if machine == "arm64" else "osx-64"
    if system == "Windows":
        return "win-64"
    return f"{system.lower()}-{machine}"


def _pixi_spec() -> Path:
    """Return the packaged `benchmark.pixi.toml` path."""
    spec = files("ai_almanac.envs").joinpath("benchmark.pixi.toml")
    return Path(str(spec))


def _blending_pixi_spec() -> Path:
    spec = files("ai_almanac.envs").joinpath("blending.pixi.toml")
    return Path(str(spec))


def _forecast_pixi_spec() -> Path:
    spec = files("ai_almanac.envs").joinpath("forecast.pixi.toml")
    return Path(str(spec))


def _require_pixi() -> str:
    pixi = shutil.which("pixi")
    if not pixi:
        raise RuntimeError("pixi is not installed. Install it from https://pixi.sh and re-run.")
    return pixi


def _install(spec: Path, env_dir: Path) -> None:
    pixi = _require_pixi()
    env_dir.mkdir(parents=True, exist_ok=True)
    target_spec = env_dir / "pixi.toml"
    target_spec.write_text(spec.read_text())
    subprocess.run(
        [pixi, "install", "--manifest-path", str(target_spec)],
        check=True,
    )


def ensure_blending_env() -> Path:
    """Install the blend dependencies and materialize its pinned source tree."""
    env_dir = blending_env_dir()
    _install(_blending_pixi_spec(), env_dir)
    source_dir = env_dir / "onset-blending"
    if not (source_dir / ".git").exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                BLENDING_REPO_URL,
                str(source_dir),
            ],
            check=True,
        )
    current = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    source_ready = (source_dir / BLENDING_SOURCE_MARKER).is_file()
    if current.returncode == 0 and current.stdout.strip() == BLENDING_REPO_REF and source_ready:
        return env_dir
    if current.returncode != 0 or current.stdout.strip() != BLENDING_REPO_REF or not source_ready:
        subprocess.run(
            [
                "git",
                "-C",
                str(source_dir),
                "fetch",
                "--refetch",
                "--depth",
                "1",
                "origin",
                BLENDING_REPO_REF,
            ],
            check=True,
        )
    subprocess.run(
        [
            "git",
            "-C",
            str(source_dir),
            "checkout",
            "--detach",
            "--force",
            BLENDING_REPO_REF,
        ],
        check=True,
    )
    if not (source_dir / BLENDING_SOURCE_MARKER).is_file():
        raise RuntimeError(
            f"Blending source checkout is incomplete: missing {BLENDING_SOURCE_MARKER}"
        )
    return env_dir


def ensure_forecast_env() -> Path | None:
    """Install the live-forecast dependencies (earth2studio + torch + geo stack).

    By far the heaviest of the three environments (CUDA/PyTorch + AI model
    checkpoints, downloaded lazily by earth2studio on first real run) — still
    chained from ensure_env() for consistency with benchmark/blending, but
    expect `ai-almanac env prepare` to take noticeably longer as a result.

    Skipped (not an error) on platforms forecast.pixi.toml doesn't support —
    a developer running `env prepare` on a Mac still gets benchmark/blending
    installed; only a Linux GPU host (see `self-host-local-gpu`) or Modal can
    actually run live forecasts.
    """
    current = _current_pixi_platform()
    if current not in _FORECAST_PLATFORMS:
        print(
            f"Skipping forecast env: unsupported on {current}. Live forecast generation "
            "needs a Linux GPU host (see the `self-host-local-gpu` deployment profile) "
            "or the Modal job runner."
        )
        return None
    pixi = _require_pixi()
    env_dir = forecast_env_dir()
    env_dir.mkdir(parents=True, exist_ok=True)
    target_spec = env_dir / "pixi.toml"
    target_spec.write_text(_forecast_pixi_spec().read_text())
    # One solve per model-group environment (they can't share one — see
    # FORECAST_ENVIRONMENTS). Each is heavy; expect this to take a while.
    for environment in FORECAST_ENVIRONMENTS:
        subprocess.run(
            [pixi, "install", "--manifest-path", str(target_spec), "-e", environment],
            check=True,
        )
    return env_dir


def ensure_env() -> tuple[Path, Path, Path | None]:
    """Idempotently prepare all local workload environments.

    Returns (benchmark_dir, blending_dir, forecast_dir) — forecast_dir is
    None when skipped on an unsupported platform (see ensure_forecast_env).
    """
    env_dir = benchmark_env_dir()
    _install(_pixi_spec(), env_dir)
    blending_dir = ensure_blending_env()
    forecast_dir = ensure_forecast_env()
    return env_dir, blending_dir, forecast_dir


def run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    """Spawn a process inside the benchmark env. Streams stdout/stderr."""
    pixi = _require_pixi()
    env_dir = benchmark_env_dir()
    full_cmd = [pixi, "run", "--manifest-path", str(env_dir / "pixi.toml"), "--", *cmd]
    return subprocess.Popen(
        full_cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def run_blending(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    """Spawn a process inside the blending environment."""
    pixi = _require_pixi()
    env_dir = blending_env_dir()
    full_cmd = [
        pixi,
        "run",
        "--manifest-path",
        str(env_dir / "pixi.toml"),
        "--",
        *cmd,
    ]
    return subprocess.Popen(
        full_cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def run_forecast(
    cmd: list[str], env: dict[str, str] | None = None, environment: str = "base"
) -> subprocess.Popen:
    """Spawn a process inside one forecast model-group environment (see
    FORECAST_ENVIRONMENTS). `environment` is the model's `env` field."""
    pixi = _require_pixi()
    env_dir = forecast_env_dir()
    full_cmd = [
        pixi,
        "run",
        "--manifest-path",
        str(env_dir / "pixi.toml"),
        "-e",
        environment,
        "--",
        *cmd,
    ]
    return subprocess.Popen(
        full_cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def env_versions() -> dict[str, str]:
    """Report installed versions of the key packages in the benchmark env."""
    pixi = _require_pixi()
    env_dir = benchmark_env_dir()
    if not (env_dir / "pixi.toml").exists():
        return {"<env>": "not prepared — run `ai-almanac env prepare`"}

    proc = subprocess.run(
        [
            pixi,
            "run",
            "--manifest-path",
            str(env_dir / "pixi.toml"),
            "--",
            "python",
            "-c",
            "import importlib.metadata as m, sys;"
            "names=['momp','xarray','netcdf4','numpy','fsspec'];"
            "\nfor n in names:\n"
            " try: print(n, m.version(n))\n"
            " except m.PackageNotFoundError: print(n, 'not installed')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    versions: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if " " in line:
            name, ver = line.split(None, 1)
            versions[name] = ver
    if not versions:
        versions["<env>"] = "prepared, but no packages found"
    return versions
