"""Run model-written Python on this machine under Anthropic's sandbox-runtime (`srt`).

`srt` compiles one policy into Seatbelt on macOS and bubblewrap on Linux: no network, the
home and data directories unreadable except for the paths a run is given, and writes only
to its output and scratch directories. Code runs with the pinned analysis environment's
interpreter (envs/analysis.pixi.toml), never the server's. When `srt` or that environment
is missing, or srt cannot start a sandbox here, code execution is unavailable rather than
unsandboxed.

ponytail: no memory or CPU cap beyond the timeout. RLIMIT_AS would also bind srt's own
Node process; use a cgroup (systemd-run --user -p MemoryMax=...) if runaway memory bites.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ai_almanac.envs.manager import (
    analysis_env_digest,
    analysis_env_unavailable_reason,
    analysis_python,
)
from ai_almanac.paths import analysis_env_dir, cache_dir, data_root, sandbox_env_dir
from ai_almanac.settings import settings

_PROBE_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class SandboxedRun:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class SandboxResult:
    """A finished sandboxed run and the files it left, copied where the sandbox never reached."""

    run: SandboxedRun
    outputs: list[Path]
    # Where it ran, as the step's provenance records it: the sandbox and its environment.
    runtime: dict[str, str]


class SandboxUnavailable(RuntimeError):
    """The sandbox could not be reached to run anything; the message is safe to show."""


class SandboxRunner(Protocol):
    """Runs a server-written script over read-only copies of ``inputs``.

    The script reads ``WORKSPACE_INPUTS`` (JSON: each input's label to the path of its
    copy), writes its results to ``ARTIFACT_DIR`` and works in ``SCRATCH_DIR``. The
    regular files it leaves in ``ARTIFACT_DIR`` are copied into ``into``, a directory
    the sandbox never had, and are the result's ``outputs``."""

    # Whether a run can be let through to ``allowed_domains``; otherwise it has no network.
    reaches_network: bool

    def ready(self) -> bool:
        """Whether it can run anything on this host right now."""
        ...

    async def run(
        self,
        script: str,
        inputs: Mapping[str, Path],
        into: Path,
        *,
        timeout: float,
        allowed_domains: tuple[str, ...] = (),
    ) -> SandboxResult: ...


def configured_runner() -> SandboxRunner | None:
    """Where this deployment is set to run sandboxed code; None when it runs none."""
    if settings.code_sandbox == "modal":
        from .modal_code_sandbox import ModalSandbox

        return ModalSandbox()
    if settings.code_sandbox == "srt":
        return LocalSandbox()
    return None


class LocalSandbox:
    """srt on this machine, with network only to a run's ``allowed_domains``."""

    reaches_network = True

    def ready(self) -> bool:
        return sandbox_unavailable_reason() is None

    async def run(
        self,
        script: str,
        inputs: Mapping[str, Path],
        into: Path,
        *,
        timeout: float,
        allowed_domains: tuple[str, ...] = (),
    ) -> SandboxResult:
        return await asyncio.to_thread(
            run_script, script, inputs, into, timeout=timeout, allowed_domains=allowed_domains
        )


def run_script(
    script: str,
    inputs: Mapping[str, Path],
    into: Path,
    *,
    timeout: float,
    allowed_domains: tuple[str, ...] = (),
) -> SandboxResult:
    """Run ``script`` in the local sandbox under the :class:`SandboxRunner` contract.

    Everything the sandbox can touch lives in a private directory beside ``into`` that is
    removed afterwards, so only the copied outputs outlive the run."""
    work = Path(tempfile.mkdtemp(prefix=f"{into.name}_sandbox_", dir=into.parent))
    try:
        stage, artifacts, scratch = (work / name for name in ("inputs", "outputs", "scratch"))
        for directory in (stage, artifacts, scratch):
            directory.mkdir()
        copies = {
            label: str(_read_only_copy(Path(source), stage / label))
            for label, source in inputs.items()
        }
        script_path = stage / "run.py"
        script_path.write_text(script)
        run = run_python(
            script_path,
            env={
                "SCRATCH_DIR": str(scratch),
                "ARTIFACT_DIR": str(artifacts),
                "WORKSPACE_INPUTS": json.dumps(copies),
            },
            readable=[stage],
            writable=[artifacts, scratch],
            timeout=timeout,
            allowed_domains=allowed_domains,
        )
        runtime = {"sandbox": "srt", "env_digest": analysis_env_digest()}
        return SandboxResult(run, take_outputs(artifacts, into), runtime)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _read_only_copy(source: Path, parent: Path) -> Path:
    """A copy of a staged file, or a directory of files, inside ``parent``. The sandbox
    can never write the stage; a file copy is made read-only besides."""
    parent.mkdir(parents=True)
    target = parent / source.name
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        shutil.copyfile(source, target)
        target.chmod(0o444)
    return target


def sandbox_runtime() -> str | None:
    """SANDBOX_RUNTIME_BIN, else the managed env's srt, else srt on PATH."""
    return shutil.which(settings.sandbox_runtime_bin or "srt", path=_search_path())


def _search_path() -> str:
    """PATH with the managed sandbox env first, so srt runs on its pinned Node and helpers.

    ponytail: Unix layout of a pixi env; the managed env is never built on Windows."""
    env_dir = sandbox_env_dir()
    managed = [env_dir / "node_modules" / ".bin", env_dir / ".pixi" / "envs" / "default" / "bin"]
    return os.pathsep.join([*map(str, managed), os.environ.get("PATH", "")])


@functools.cache
def sandbox_unavailable_reason() -> str | None:
    """Why sandboxed code cannot run on this host, or None when it can. Probed once."""
    srt = sandbox_runtime()
    if srt is None:
        return "sandbox-runtime (srt) is not installed; run `ai-almanac env prepare`"
    if (reason := analysis_env_unavailable_reason()) is not None:
        return reason
    with tempfile.TemporaryDirectory() as scratch:
        script = Path(scratch) / "probe.py"
        script.write_text("print('ok')\n")
        try:
            run = _run(srt, script, env={}, readable=[script.parent], writable=[], timeout=None)
        except OSError as exc:
            return f"srt could not be started: {exc}"
    if run.stdout.strip() == "ok":
        return None
    detail = (run.stderr or run.stdout).strip()[-500:]
    return f"srt could not start a sandbox on this host: {detail or 'no output'}"


def recheck_sandbox() -> str | None:
    """Probe again, since the host may have been prepared since the cached probe."""
    sandbox_unavailable_reason.cache_clear()
    return sandbox_unavailable_reason()


def run_python(
    script: Path,
    *,
    env: dict[str, str],
    readable: list[Path],
    writable: list[Path],
    timeout: float,
    allowed_domains: tuple[str, ...] = (),
) -> SandboxedRun:
    """Run `script` with the analysis environment's interpreter inside the sandbox.

    `readable` and `writable` are the only paths under the home and data directories the
    process can see; `writable` paths are also readable. The network is closed except to
    `allowed_domains`, reached through srt's proxy; only server-written scripts get any.
    `env` reaches srt as well as the script, so leave TMPDIR alone: srt puts its proxy
    sockets there, and macOS caps socket paths at 104 bytes. Callers must check
    `sandbox_unavailable_reason()` first."""
    srt = sandbox_runtime()
    if srt is None:
        raise RuntimeError("sandbox-runtime (srt) is not installed")
    mpl_cache = _warm_matplotlib_cache()
    env = {"MPLBACKEND": "Agg", "MPLCONFIGDIR": str(mpl_cache), **env}
    return _run(
        srt,
        script,
        env=env,
        readable=[*readable, mpl_cache],
        writable=writable,
        timeout=timeout,
        allowed_domains=allowed_domains,
    )


def take_outputs(sandbox_dir: Path, into: Path) -> list[Path]:
    """Copy the regular files sandboxed code left in `sandbox_dir` into `into`, a directory
    the sandbox never had, and return the copies by name.

    The host reads outputs with its own privileges, and the sandbox restricts reads but not
    where a symlink or hard link it writes points. So a link would hand the host any file it
    can read; links, subdirectories and anything else that is not a single-linked regular
    file are skipped, and nothing downstream touches a path the sandbox controlled.

    That holds for the directory as much as for what is in it: the sandbox may write its own
    root, and on macOS that lets it replace the directory with a link to anywhere. So the
    directory is opened without following a link and read through that one handle. A
    directory that has become a link has no outputs."""
    try:
        directory = os.open(sandbox_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return []
    try:
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries if entry.is_file(follow_symlinks=False))
        copies = (_copy_regular_file(name, directory, into / name) for name in names)
        return [copy for copy in copies if copy is not None]
    finally:
        os.close(directory)


def _copy_regular_file(name: str, directory: int, target: Path) -> Path | None:
    """Copy the file called `name` in the open `directory`, unless it is a link."""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as reader:
        info = os.fstat(reader.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        with target.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
    return target


def sandbox_settings(
    readable: list[Path], writable: list[Path], allowed_domains: tuple[str, ...] = ()
) -> dict:
    interpreter = {analysis_env_dir(), analysis_python().resolve().parent}
    # On Linux srt runs its socat bridge and seccomp loader inside the sandbox, from here.
    tools = sandbox_env_dir()
    return {
        "network": {"allowedDomains": list(allowed_domains), "deniedDomains": []},
        "filesystem": {
            "denyRead": [str(Path.home()), str(data_root())],
            "allowRead": sorted({str(p) for p in (*interpreter, tools, *readable, *writable)}),
            "allowWrite": [str(p) for p in writable],
            "denyWrite": [],
        },
    }


def _run(
    srt: str,
    script: Path,
    *,
    env: dict[str, str],
    readable: list[Path],
    writable: list[Path],
    timeout: float | None,
    allowed_domains: tuple[str, ...] = (),
) -> SandboxedRun:
    # The policy file lives outside every writable path, so the sandboxed code cannot edit it.
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(sandbox_settings(readable, writable, allowed_domains), handle)
    try:
        proc = subprocess.run(
            [srt, "--settings", handle.name, str(analysis_python()), "-I", str(script)],
            env={"PATH": _search_path(), **env},
            cwd=script.parent,
            capture_output=True,
            text=True,
            timeout=timeout or _PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxedRun(None, _text(exc.stdout), _text(exc.stderr), timed_out=True)
    finally:
        Path(handle.name).unlink(missing_ok=True)
    return SandboxedRun(proc.returncode, proc.stdout, proc.stderr)


def _warm_matplotlib_cache() -> Path:
    """Build matplotlib's font cache once, outside the sandbox, from our own import.

    Runs then mount it read-only, instead of each spending ~20 s rebuilding it or
    sharing a writable directory another run could tamper with. Built by the analysis
    environment's matplotlib, the one the runs import."""
    target = cache_dir() / "analysis-matplotlib"
    if not any(target.glob("fontlist-*.json")):
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(analysis_python()), "-I", "-c", "import matplotlib.font_manager"],
            env={"PATH": os.environ.get("PATH", ""), "MPLCONFIGDIR": str(target)},
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS * 2,
        )
    return target


def _text(value: bytes | str | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
