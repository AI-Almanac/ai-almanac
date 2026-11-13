"""Run model-written Python in a network-blocked Modal Sandbox (cloud deployments).

Cloud Run cannot host sandbox-runtime, so with `code_sandbox: modal` workspace
run_code runs here: one fresh Sandbox per call with no network and no secrets,
the requested inputs copied in, and only files written to the output directory
copied back. The image installs the same locked analysis environment local srt
runs use (envs/analysis.pixi.toml + analysis.pixi.lock), and its Modal image id is
the `env_digest` recorded in the run's provenance, so changing a package pin
changes every later step's seal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ai_almanac.envs.manager import analysis_pixi_lock, analysis_pixi_spec

from .code_sandbox import SandboxedRun, SandboxResult, SandboxUnavailable

logger = logging.getLogger(__name__)

APP_NAME = "almanac-workspace"
PIXI_VERSION = "0.63.1"
ENV_ROOT = "/analysis"
PYTHON = f"{ENV_ROOT}/.pixi/envs/default/bin/python"
CPU = 1.0
MEMORY_MB = 2048
INPUT_ROOT = PurePosixPath("/inputs")
HARNESS_PATH = str(INPUT_ROOT / "harness.py")
OUTPUT_DIR = "/outputs"
SCRATCH_DIR = "/scratch"


@dataclass(frozen=True)
class ModalRun:
    run: SandboxedRun
    image_id: str


class ModalSandbox:
    """The :class:`code_sandbox.SandboxRunner` for cloud deployments: no network at all."""

    reaches_network = False

    def ready(self) -> bool:
        return True

    async def run(
        self,
        script: str,
        inputs: Mapping[str, Path],
        into: Path,
        *,
        timeout: float,
        allowed_domains: tuple[str, ...] = (),
    ) -> SandboxResult:
        if allowed_domains:
            raise ValueError("A Modal sandbox has no network to allow domains on")
        staged = {label: str(path) for label, path in inputs.items()}
        try:
            finished = await run_harness(script, staged, into, timeout=int(timeout))
        except Exception as exc:
            logger.exception("Modal sandbox run failed")
            raise SandboxUnavailable("Code execution is unavailable right now") from exc
        # Only _collect writes here, and only regular files it read out of the sandbox.
        outputs = sorted(path for path in into.iterdir() if path.is_file())
        runtime = {"sandbox": "modal", "env_digest": finished.image_id}
        return SandboxResult(finished.run, outputs, runtime)


def image():
    import modal

    return (
        modal.Image.debian_slim()
        .apt_install("curl", "ca-certificates", "git")
        .run_commands(
            "curl -fsSL https://pixi.sh/install.sh | "
            f"PIXI_VERSION=v{PIXI_VERSION} PIXI_HOME=/usr/local PIXI_NO_PATH_UPDATE=1 bash"
        )
        .add_local_file(analysis_pixi_spec(), f"{ENV_ROOT}/pixi.toml", copy=True)
        .add_local_file(analysis_pixi_lock(), f"{ENV_ROOT}/pixi.lock", copy=True)
        .run_commands(f"pixi install --locked --manifest-path {ENV_ROOT}/pixi.toml")
        .env({"MPLBACKEND": "Agg"})
    )


def remote_inputs(staged: dict[str, str]) -> dict[str, str]:
    """Where each staged input lands inside the Sandbox, keyed by artifact label."""
    return {label: str(INPUT_ROOT / label / Path(source).name) for label, source in staged.items()}


async def run_harness(
    script: str, staged: dict[str, str], out_dir: Path, *, timeout: int
) -> ModalRun:
    """Run `script` in a new Sandbox over copies of `staged`; copy its outputs into `out_dir`."""
    import modal

    app = await modal.App.lookup.aio(APP_NAME, create_if_missing=True)
    sandbox_image = image()
    sandbox = await modal.Sandbox.create.aio(
        app=app,
        image=sandbox_image,
        block_network=True,
        cpu=CPU,
        memory=MEMORY_MB,
        timeout=timeout + 60,
    )
    try:
        inputs = await _stage(sandbox, script, staged)
        env = {
            "ARTIFACT_DIR": OUTPUT_DIR,
            "SCRATCH_DIR": SCRATCH_DIR,
            "WORKSPACE_INPUTS": json.dumps(inputs),
        }
        try:
            run = await asyncio.wait_for(_exec(sandbox, env), timeout)
        except TimeoutError:
            run = SandboxedRun(None, "", "", timed_out=True)
        else:
            await _collect(sandbox, out_dir)
        return ModalRun(run, sandbox_image.object_id)
    finally:
        await sandbox.terminate.aio()


async def _stage(sandbox, script: str, staged: dict[str, str]) -> dict[str, str]:
    inputs = remote_inputs(staged)
    for label, source in staged.items():
        await sandbox.filesystem.copy_from_local.aio(source, inputs[label])
    await sandbox.filesystem.write_text.aio(script, HARNESS_PATH)
    for directory in (OUTPUT_DIR, SCRATCH_DIR):
        await sandbox.filesystem.make_directory.aio(directory)
    return inputs


async def _exec(sandbox, env: dict[str, str]) -> SandboxedRun:
    process = await sandbox.exec.aio(PYTHON, "-I", HARNESS_PATH, env=env, workdir=SCRATCH_DIR)
    stdout, stderr = await asyncio.gather(process.stdout.read.aio(), process.stderr.read.aio())
    return SandboxedRun(await process.wait.aio(), stdout, stderr)


async def _collect(sandbox, out_dir: Path) -> None:
    """Copy regular files from the output directory; symlinks and subdirectories are ignored."""
    for entry in await sandbox.filesystem.list_files.aio(OUTPUT_DIR):
        if entry.is_file():
            await sandbox.filesystem.copy_to_local.aio(entry.path, out_dir / Path(entry.name).name)
