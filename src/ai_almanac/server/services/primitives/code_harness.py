"""Code in the sandbox: the harness every sandboxed script runs inside, and ``run_code``.

The harness defines ``compute(inputs)`` around the code, gives it ``save_figure``, and
prints one JSON line with the result. Where it runs is the deployment's sandbox
(``code_sandbox.configured_runner``); this module never picks a sandbox itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import RunContext

from ai_almanac.settings import settings

from .. import code_sandbox, ledger, provenance, workspaces
from .registry import (
    Refusal,
    listed_title,
    output_dir,
    primitive,
    stage_into,
    work_dir,
    workspace_artifact,
)

# What the sandbox gives custom code for saving a chart. The title is `label`, else the
# one the code drew, and it travels back with the result so the workspace lists the
# figure by it. Reads ARTIFACT_DIR and `os` from the harness around it.
SAVE_FIGURE_SOURCE = """
FIGURE_TITLES = {}

def _drawn_title(fig):
    heading = fig.get_suptitle() if hasattr(fig, "get_suptitle") else ""
    if heading.strip():
        return heading.strip()
    titles = [ax.get_title().strip() for ax in fig.axes if ax.get_title().strip()]
    return titles[0] if len(titles) == 1 else None

def save_figure(fig, filename="figure.png", label=None, **kwargs):
    name = os.path.basename(filename)
    fig.savefig(os.path.join(ARTIFACT_DIR, name), **kwargs)
    title = label or _drawn_title(fig)
    if title:
        FIGURE_TITLES[name] = str(title)
    return {"kind": "figure", "filename": name, "label": label}
"""

_HARNESS = """
import json, os, sys, tempfile, traceback
os.environ["HOME"] = os.environ["TMPDIR"] = tempfile.tempdir = os.environ["SCRATCH_DIR"]
ARTIFACT_DIR = os.environ["ARTIFACT_DIR"]
INPUTS = json.loads(os.environ["WORKSPACE_INPUTS"])

{save_figure}

{code}

try:
    result = compute(INPUTS)
    if not isinstance(result, dict):
        result = {"value": result}
    result.pop("artifacts", None)
    titled = {"figure_titles": FIGURE_TITLES} if FIGURE_TITLES else {}
    print(json.dumps({"ok": True, "result": result, **titled}, default=str))
except Exception:
    print(json.dumps({"ok": False, "error": traceback.format_exc()[-4000:]}))
""".replace("{save_figure}", SAVE_FIGURE_SOURCE)


def code_execution_available() -> bool:
    """Workspace run_code is offered wherever the configured sandbox can run it.

    Independent of ``enable_run_code``, which gates the page-chat tool and is forced
    off in shared mode: that one runs unsandboxed next to GCP credentials, while
    both code sandboxes have no network and no secrets, so they are safe to share.
    """
    runner = code_sandbox.configured_runner()
    return runner is not None and runner.ready()


def harness_outcome(run: code_sandbox.SandboxedRun) -> dict:
    """The JSON line a sandboxed script ends with; a failure in words when it has none."""
    if run.timed_out:
        return {"ok": False, "error": "run_code timed out"}
    stdout = run.stdout.strip()
    if not stdout:
        return {"ok": False, "error": run.stderr[-4000:] or "Code produced no output"}
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Non-JSON output: {stdout[-2000:]}"}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "Invalid payload"}


@dataclass(frozen=True)
class HarnessRun:
    """What the code returned, the files it wrote (copied out) and where it ran."""

    outcome: dict
    outputs: list[Path]
    runtime: dict[str, str]


async def run_in_harness(code: str, inputs: Mapping[str, Path], into: Path) -> HarnessRun:
    """Run ``code`` in the harness in this deployment's sandbox; its files land in ``into``."""
    runner = code_sandbox.configured_runner()
    if runner is None:
        raise Refusal("Code execution is not available on this deployment", ok=False)
    try:
        result = await runner.run(
            _HARNESS.replace("{code}", code),
            inputs,
            into,
            timeout=settings.local_code_timeout_seconds,
        )
    except code_sandbox.SandboxUnavailable as exc:
        raise Refusal(str(exc), ok=False) from exc
    return HarnessRun(harness_outcome(result.run), result.outputs, result.runtime)


def derived_outputs(produced: list[Path], figure_titles: object) -> list[ledger.OutputFile]:
    """The files custom code wrote, each figure listed by the title it was saved with."""
    titles = figure_titles if isinstance(figure_titles, Mapping) else {}
    return [
        ledger.OutputFile(
            path=path,
            label=workspaces.safe_label(f"derived_{path.stem}"),
            title=listed_title(str(titles[path.name])) if titles.get(path.name) else None,
        )
        for path in produced
    ]


def _code_file(code: str, code_hash: str, directory: Path) -> ledger.OutputFile:
    """The code itself, kept beside its results so their history points at it."""
    source = directory / "run_code.py"
    source.write_text(code)
    return ledger.OutputFile(
        path=source,
        label=f"code_{code_hash[:8]}",
        kind="document",
        title="Code that was run",
        supporting=True,
    )


@primitive("code.run_code", "1.1.0", label_args=("inputs",), available=code_execution_available)
async def run_code(ctx: RunContext, code: str, inputs: list[str] | None = None) -> dict:
    """Run Python against workspace artifacts. Define `compute(inputs: dict[str, str]) -> dict`
    where `inputs` maps each requested artifact label to a read-only file path. Anything written
    to the `ARTIFACT_DIR` directory (or via `save_figure(fig, filename, label)`) becomes an
    artifact labelled as derived, unverified computation. A figure is listed in the workspace
    under `label`, so make it the figure's title in words; without one it takes the title
    drawn on the figure. Prefer typed primitives when one exists; use this for analysis they
    cannot express. Return small summaries, not bulk data."""
    rows = [await workspace_artifact(ctx, ref) for ref in inputs or []]
    with work_dir("run_code_inputs") as downloads, output_dir("run_code") as out_dir:
        staged = {row["label"]: await stage_into(row, downloads) for row in rows}
        ran = await run_in_harness(code, staged, out_dir)
        if not ran.outcome.get("ok"):
            raise Refusal(ran.outcome.get("error", "run_code failed"), ok=False)
    for path in ran.outputs:
        provenance.strip_history(path)
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    with output_dir("run_code_source") as source_dir:
        code_file = _code_file(code, code_hash, source_dir)
    figures = [
        {"kind": "figure", "data": path.read_bytes(), "filename": path.name, "label": path.stem}
        for path in ran.outputs
        if provenance.kind_of(path) == "figure"
    ]
    return {
        "ok": True,
        "result": ran.outcome.get("result"),
        "artifacts": figures,
        "_outputs": [code_file, *derived_outputs(ran.outputs, ran.outcome.get("figure_titles"))],
        # The role is the key the code reads each input by, which is part of what it computed.
        "_inputs": [(row["label"], row) for row in rows],
        "_code_hash": code_hash,
        "_runtime": ran.runtime,
    }
