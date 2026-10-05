"""Pixi-managed scientific workload environments.

The benchmark environment carries ROMP and its scientific dependencies
separately from the ai-almanac web server's own environment. This keeps
`pip install ai-almanac` small and isolates FastAPI from the benchmark stack.

The env lives at `$AI_ALMANAC_DATA_DIR/benchmark-env/` and is prepared on
first use via `ai-almanac env prepare`.
"""

from __future__ import annotations

import os
import subprocess
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from ai_almanac.envs.pixi_bootstrap import (
    _current_pixi_platform,
    ensure_pixi,
)
from ai_almanac.locking import file_lock
from ai_almanac.paths import (
    benchmark_env_dir,
    blending_env_dir,
    env_root,
    forecast_env_dir,
    llm_env_dir,
)

BLENDING_REPO_URL = "https://github.com/hholb/onset_blending-adm3.git"
# Keep in sync with modal/blending_app.py DEFAULT_REPO_REF; tests/test_blending_pins.py
# enforces it. See docs/onset-blending-haiyang-integration.md for the pin history.
BLENDING_REPO_REF = "2a59cec0680dcfb575104fa03b59ee64dc110f82"
BLENDING_SOURCE_MARKER = Path("python/prepare_data/nc_utils.py")

_FORECAST_PLATFORMS = ("linux-64", "linux-aarch64")
FORECAST_ENVIRONMENTS = ("base", "aifs2", "aifs2ens")

EventKind = Literal["phase_started", "line", "phase_finished", "phase_skipped", "phase_failed"]


@dataclass(frozen=True, slots=True)
class EnvProgressEvent:
    kind: EventKind
    phase: str
    line: str | None = None
    detail: str | None = None


ProgressCallback = Callable[[EnvProgressEvent], None]


def _default_progress(event: EnvProgressEvent) -> None:
    if event.kind == "phase_started":
        print(f"==> {event.phase}" + (f": {event.detail}" if event.detail else ""))
    elif event.kind == "line":
        print(event.line or "", end="" if (event.line or "").endswith("\n") else "\n", flush=True)
    elif event.kind == "phase_finished":
        print(f"==> {event.phase} ready at {event.detail}")
    elif event.kind == "phase_skipped":
        print(f"==> {event.phase} skipped: {event.detail}")
    elif event.kind == "phase_failed":
        print(f"==> {event.phase} FAILED")
        if event.detail:
            print(event.detail)


def _ensure_pixi(progress: ProgressCallback | None = None) -> str:
    return ensure_pixi(progress=progress)


def _run_streaming(
    cmd: list[str],
    phase: str,
    progress: ProgressCallback,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    progress(EnvProgressEvent(kind="phase_started", phase=phase))
    tail: deque[str] = deque(maxlen=30)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env,
    )
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        tail.append(raw_line)
        progress(EnvProgressEvent(kind="line", phase=phase, line=raw_line))
    proc.wait()
    if proc.returncode != 0:
        tail_str = "".join(tail)
        progress(EnvProgressEvent(kind="phase_failed", phase=phase, detail=tail_str))
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=tail_str)
    progress(EnvProgressEvent(kind="phase_finished", phase=phase))


def _pixi_spec() -> Path:
    spec = files("ai_almanac.envs").joinpath("benchmark.pixi.toml")
    return Path(str(spec))


def _blending_pixi_spec() -> Path:
    spec = files("ai_almanac.envs").joinpath("blending.pixi.toml")
    return Path(str(spec))


def _forecast_pixi_spec() -> Path:
    spec = files("ai_almanac.envs").joinpath("forecast.pixi.toml")
    return Path(str(spec))


def _llm_pixi_spec() -> Path:
    spec = files("ai_almanac.envs").joinpath("llm.pixi.toml")
    return Path(str(spec))


def _install(
    spec: Path,
    env_dir: Path,
    phase: str,
    progress: ProgressCallback,
    environments: tuple[str, ...] | None = None,
) -> None:
    pixi = _ensure_pixi(progress)
    env_dir.mkdir(parents=True, exist_ok=True)
    target_spec = env_dir / "pixi.toml"
    target_spec.write_text(spec.read_text())
    if environments:
        for env in environments:
            # pixi runs pypi build hooks without activating the env, so
            # no-build-isolation sdists (flash-attn) can't find the env's
            # conda toolchain. Point CUDA_HOME/PATH at the env prefix —
            # its conda packages (nvcc, ninja) are installed before the
            # pypi build phase of the same `pixi install` run.
            prefix = env_dir / ".pixi" / "envs" / env
            build_env = {
                **os.environ,
                "CUDA_HOME": str(prefix),
                "PATH": f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            _run_streaming(
                [pixi, "install", "--manifest-path", str(target_spec), "-e", env],
                phase=f"forecast:{env}",
                progress=progress,
                cwd=env_dir,
                env=build_env,
            )
    else:
        _run_streaming(
            [pixi, "install", "--manifest-path", str(target_spec)],
            phase=phase,
            progress=progress,
            cwd=env_dir,
        )


def ensure_blending_env(progress: ProgressCallback | None = None) -> Path:
    """Install the blend dependencies and materialize its pinned source tree."""
    progress = progress or _default_progress
    with file_lock(
        env_root() / ".prepare.lock",
        message="another instance is preparing environments in this env root; waiting…",
    ):
        return _ensure_blending_env(progress)


def _ensure_blending_env(progress: ProgressCallback) -> Path:
    env_dir = blending_env_dir()
    _install(_blending_pixi_spec(), env_dir, "blending", progress)
    source_dir = env_dir / "onset-blending"
    if not (source_dir / ".git").exists():
        _run_streaming(
            [
                "git",
                "clone",
                "--filter=blob:none",
                BLENDING_REPO_URL,
                str(source_dir),
            ],
            phase="blending-source",
            progress=progress,
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
    _run_streaming(
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
        phase="blending-source",
        progress=progress,
    )
    _run_streaming(
        [
            "git",
            "-C",
            str(source_dir),
            "checkout",
            "--detach",
            "--force",
            BLENDING_REPO_REF,
        ],
        phase="blending-source",
        progress=progress,
    )
    if not (source_dir / BLENDING_SOURCE_MARKER).is_file():
        raise RuntimeError(
            f"Blending source checkout is incomplete: missing {BLENDING_SOURCE_MARKER}"
        )
    return env_dir


def ensure_forecast_env(progress: ProgressCallback | None = None) -> Path | None:
    """Install the live-forecast dependencies (earth2studio + torch + geo stack).

    Skipped (not an error) on platforms forecast.pixi.toml doesn't support.
    """
    progress = progress or _default_progress
    with file_lock(
        env_root() / ".prepare.lock",
        message="another instance is preparing environments in this env root; waiting…",
    ):
        return _ensure_forecast_env(progress)


def _ensure_forecast_env(progress: ProgressCallback) -> Path | None:
    current = _current_pixi_platform()
    if current not in _FORECAST_PLATFORMS:
        progress(
            EnvProgressEvent(
                kind="phase_skipped",
                phase="forecast",
                detail=(
                    f"unsupported on {current}. Live forecast generation needs a Linux "
                    "GPU host (see `self-host-local-gpu`) or the Modal job runner."
                ),
            )
        )
        return None
    env_dir = forecast_env_dir()
    _install(
        _forecast_pixi_spec(),
        env_dir,
        phase="forecast",
        progress=progress,
        environments=FORECAST_ENVIRONMENTS,
    )
    return env_dir


def ensure_llm_env(progress: ProgressCallback | None = None) -> Path | None:
    """Install the bundled vLLM server env. Skipped where vLLM has no build."""
    progress = progress or _default_progress
    with file_lock(
        env_root() / ".prepare.lock",
        message="another instance is preparing environments in this env root; waiting…",
    ):
        return _ensure_llm_env(progress)


def _ensure_llm_env(progress: ProgressCallback) -> Path | None:
    # Same gate as the forecast env: vLLM ships Linux CUDA wheels only.
    current = _current_pixi_platform()
    if current not in _FORECAST_PLATFORMS:
        progress(
            EnvProgressEvent(
                kind="phase_skipped",
                phase="llm",
                detail=(
                    f"unsupported on {current}. The bundled model server needs a Linux "
                    "GPU host; connect to an existing OpenAI-compatible server instead."
                ),
            )
        )
        return None
    env_dir = llm_env_dir()
    _install(_llm_pixi_spec(), env_dir, "llm", progress)
    return env_dir


def llm_env_ready() -> bool:
    env_dir = llm_env_dir()
    return (env_dir / "pixi.toml").exists() and (env_dir / ".pixi" / "envs" / "default").exists()


def download_llm_model(model_id: str, progress: ProgressCallback | None = None) -> None:
    """Fetch a model's weights into the Hugging Face cache ahead of serving.

    Idempotent: `hf download` skips files already in the cache.
    """
    progress = progress or _default_progress
    pixi = _ensure_pixi(progress)
    env_dir = llm_env_dir()
    _run_streaming(
        [
            pixi,
            "run",
            "--manifest-path",
            str(env_dir / "pixi.toml"),
            "--",
            "hf",
            "download",
            model_id,
        ],
        phase="llm-model",
        progress=progress,
        cwd=env_dir,
    )


def ensure_env(
    progress: ProgressCallback | None = None,
    include_forecast: bool = True,
    include_llm: bool = False,
) -> tuple[Path, Path, Path | None]:
    """Idempotently prepare all local workload environments.

    Returns (benchmark_dir, blending_dir, forecast_dir) — forecast_dir is
    None when skipped on an unsupported platform or when include_forecast=False.
    include_llm also installs the bundled vLLM env (see llm_env_dir()); model
    weights are fetched separately by download_llm_model.
    """
    progress = progress or _default_progress
    with file_lock(
        env_root() / ".prepare.lock",
        message="another instance is preparing environments in this env root; waiting…",
    ):
        _ensure_pixi(progress)
        env_dir = benchmark_env_dir()
        _install(_pixi_spec(), env_dir, "benchmark", progress)
        blending_dir = _ensure_blending_env(progress)
        forecast_dir = _ensure_forecast_env(progress) if include_forecast else None
        if include_llm:
            _ensure_llm_env(progress)
    return env_dir, blending_dir, forecast_dir


def run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    """Spawn a process inside the benchmark env. Streams stdout/stderr."""
    pixi = _ensure_pixi()
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
    pixi = _ensure_pixi()
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
    """Spawn a process inside one forecast model-group environment."""
    pixi = _ensure_pixi()
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


def run_llm_server(
    args: list[str], env: dict[str, str] | None = None, log_path: Path | None = None
) -> subprocess.Popen:
    """Spawn the vLLM server inside the LLM env, detached in its own session.

    Unlike the workload runners this is long-lived, so output goes to a log
    file (a pipe nobody drains would eventually block the server). The new
    session makes the process group killable as one unit, EngineCore included.
    """
    pixi = _ensure_pixi()
    env_dir = llm_env_dir()
    full_cmd = [pixi, "run", "--manifest-path", str(env_dir / "pixi.toml"), "--", *args]
    log_file = log_path.open("a") if log_path else subprocess.DEVNULL
    try:
        return subprocess.Popen(
            full_cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        if log_path:
            log_file.close()


def env_versions() -> dict[str, str]:
    """Report installed versions of the key packages in the benchmark env."""
    env_dir = benchmark_env_dir()
    if not (env_dir / "pixi.toml").exists():
        return {"<env>": "not prepared — run `ai-almanac env prepare`"}

    pixi = _ensure_pixi()
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
