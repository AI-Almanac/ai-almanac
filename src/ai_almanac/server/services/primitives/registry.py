"""The ``@primitive`` chokepoint every workspace capability goes through.

A primitive is a plain async function over ``RunContext[ChatDeps]``. The decorator
refuses a turn whose workspace is not the caller's, raises ``ApprovalRequired`` when a
primitive needs one, and, when the body returns files, writes one ledger step carrying
the params, the role-keyed input hashes, the turn that proposed it and the approval,
then adds the files to the workspace. The body never writes provenance (P1).

A body returns a plain dict for the model, or raises :class:`Refusal` with words for
it. Reserved keys are consumed by the decorator and never reach the model:

- ``_outputs``: ``list[ledger.OutputFile]`` to store,
- ``_inputs``: ``(role, artifact row)`` pairs they were computed from,
- ``_params``: what the body resolved from the arguments (e.g. a source's name),
  recorded alongside them,
- ``_runtime`` and ``_code_hash``: where and what code the step ran, sealed into it.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_core import to_jsonable_python

from ai_almanac import __version__
from ai_almanac.paths import data_root

from .. import approval_display, approval_gate, ledger, read_sets, workspaces
from ..approval_display import Fact
from ..chat_tools import tool_payload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrimitiveSpec:
    name: str
    version: str
    needs_approval: bool
    fn: Callable[..., Awaitable[dict]]
    doc: str
    available: Callable[[], bool]
    # The module of the primitives package that defines it, so each can be offered alone.
    section: str


REGISTRY: dict[str, PrimitiveSpec] = {}

# Receives the RunContext and the primitive's own validated keyword arguments, and
# returns the approval card's facts in the user's words.
Describer = Callable[..., Awaitable[list[Fact]]]

SETTINGS_MOVED = (
    "This step was not run: the workspace's settings changed after it was shown for "
    "approval, so it would no longer do what was approved. Propose it again."
)


class Refusal(Exception):
    """A call the primitive will not carry out, with what to tell the model instead.

    ``details`` travel beside the error, e.g. ``ok=False`` for a primitive whose
    results carry ``ok``, or the last day a source has data for."""

    def __init__(self, error: str, **details: object) -> None:
        super().__init__(error)
        self.result = {"error": error, **details}


def json_params(kwargs: dict) -> dict:
    """Arguments as the approval card and the ledger hold them."""
    return to_jsonable_python(kwargs, fallback=str)


def primitive(
    name: str,
    version: str,
    *,
    needs_approval: bool = False,
    label_args: tuple[str, ...] = (),
    available: Callable[[], bool] = lambda: True,
    precheck: Callable[..., Awaitable[None]] | None = None,
    describe: Describer | None = None,
    resolve: Callable[..., Awaitable[dict]] | None = None,
):
    """Register ``fn`` as a workspace primitive and wrap it with the provenance chokepoint.

    ``available`` says whether this deployment can run it; unavailable primitives are
    left out of the toolset. ``precheck`` sees the arguments before approval is asked
    for and raises :class:`Refusal` for a call that must not reach the user.

    ``describe`` writes what an approval card shows: ``await describe(ctx, **arguments)``
    returns ``approval_display.Fact`` s, names instead of ids. They travel as the
    approval's ``display``, beside the hashed ``params`` and never part of them.
    Without one, every param is shown with the ids it can resolve named.

    ``resolve`` fills in what a call leaves to the workspace's settings:
    ``await resolve(ctx, **arguments)`` returns the arguments the step runs with. It
    runs before approval is asked for, so the card and the approval cover the settings
    the step uses. A step whose settings moved between the card and the approval is
    not run.

    ``label_args`` name the arguments that are workspace labels. They are left out
    of the recorded params: the body reports what it read as ``_inputs``, and the
    ledger records those by role and artifact id, never by label (D-R11).
    """

    def decorate(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(ctx: RunContext, **kwargs) -> dict:
            deps = ctx.deps
            if deps.workspace_id is None:
                return {"error": workspaces.NOT_IN_CONVERSATION}
            try:
                if resolve is not None:
                    kwargs = await resolve(ctx, **kwargs)
                if precheck is not None:
                    await precheck(**kwargs)
            except Refusal as refusal:
                return await tool_payload(refusal.result, deps.session_id, deps.user_id)
            params = json_params(kwargs)

            async def run() -> dict:
                try:
                    result = await fn(ctx, **kwargs)
                except Refusal as refusal:
                    return await tool_payload(refusal.result, deps.session_id, deps.user_id)
                outputs = result.pop("_outputs", None)
                inputs = result.pop("_inputs", [])
                runtime = result.pop("_runtime", {})
                code_hash = result.pop("_code_hash", None)
                resolved = result.pop("_params", {})
                if inputs:
                    result["source_artifacts"] = read_sets.labels_of(inputs)
                if outputs:
                    try:
                        await _record_outputs(
                            ctx,
                            ledger.StepDraft(
                                primitive=name,
                                version=version,
                                params={
                                    **{k: v for k, v in params.items() if k not in label_args},
                                    **resolved,
                                },
                                inputs=inputs,
                                code_hash=code_hash,
                                runtime={
                                    "ai_almanac": __version__,
                                    "python": sys.version.split()[0],
                                    **runtime,
                                },
                            ),
                            outputs,
                            result,
                            needs_approval=needs_approval,
                        )
                    finally:
                        discard_scratch(outputs)
                return await tool_payload(result, deps.session_id, deps.user_id)

            if not needs_approval:
                return await run()
            return await approval_gate.gated(
                ctx,
                name,
                params,
                card=lambda: _approval_card(name, describe, ctx, kwargs, params),
                run=run,
                moved={"error": SETTINGS_MOVED},
            )

        REGISTRY[name] = PrimitiveSpec(
            name=name,
            version=version,
            needs_approval=needs_approval,
            fn=wrapper,
            doc=inspect.getdoc(fn) or "",
            available=available,
            section=fn.__module__,
        )
        return wrapper

    return decorate


async def _approval_card(
    name: str, describe: Describer | None, ctx: RunContext, kwargs: dict, params: dict
) -> dict:
    try:
        if describe is None:
            shown = await approval_display.describe_generic(ctx.deps.user_id, params)
        else:
            shown = await describe(ctx, **kwargs)
    except Exception:
        # The card's wording must never block the approval itself.
        logger.warning("Could not describe the %s approval", name, exc_info=True)
        shown = approval_display.generic_facts(params, {})
    return {"display": approval_display.display_block(shown)}


async def _record_outputs(
    ctx: RunContext,
    draft: ledger.StepDraft,
    outputs: list[ledger.OutputFile],
    result: dict,
    *,
    needs_approval: bool,
) -> None:
    """Write the step to the ledger, then show its outputs in the workspace."""
    deps = ctx.deps
    attribution = ledger.Attribution.by_assistant(
        deps.user_id, deps.session_id, approved=needs_approval, turn=deps.turn
    )
    approval_id = await ledger.record_approval(attribution, draft.primitive, draft.params)
    step, artifacts = await ledger.write_step(draft, attribution, outputs, approval_id)
    labels = await workspaces.add_artifacts(
        deps.scope.key,
        [
            (a, workspaces.NewItem(o.label, o.title, o.supporting))
            for a, o in zip(artifacts, outputs, strict=True)
        ],
    )
    result["workspace_artifacts"] = [
        {"id": a["id"], "label": label, "kind": a["kind"], "format": a["format"]}
        for a, label in zip(artifacts, labels, strict=True)
    ]
    result["provenance_step_id"] = step["id"]
    _link_figures_to_artifacts(result.get("artifacts", []), outputs, artifacts)


def _link_figures_to_artifacts(
    figures: list[dict], outputs: list[ledger.OutputFile], artifacts: list[dict]
) -> None:
    """Tag each chat figure with the workspace artifact its file was registered as."""
    by_filename = {
        output.path.name: artifact["id"]
        for output, artifact in zip(outputs, artifacts, strict=True)
    }
    for figure in figures:
        artifact_id = by_filename.get(figure.get("filename") or "")
        if artifact_id:
            figure["workspace_artifact_id"] = artifact_id


def toolset(section: ModuleType) -> FunctionToolset:
    """One section's primitives as a pydantic-ai toolset; docstrings become tool
    descriptions. Primitives this deployment cannot run are left out."""
    tools = FunctionToolset(id=f"workspace-{section.__name__.rsplit('.', 1)[-1]}")
    for spec in REGISTRY.values():
        if spec.section == section.__name__ and spec.available():
            tools.tool(spec.fn, name=spec.name.split(".")[-1])
    return tools


# --- scratch files ------------------------------------------------------------


def _scratch_root() -> Path:
    root = data_root() / "scratch"
    root.mkdir(parents=True, exist_ok=True)
    return root


def scratch_dir(name: str) -> Path:
    """A fresh directory for a primitive's output files, removed once they are stored."""
    return Path(tempfile.mkdtemp(prefix=f"{name}_", dir=_scratch_root()))


@contextlib.contextmanager
def output_dir(name: str) -> Iterator[Path]:
    """A scratch directory for output files that is removed when the step fails; on
    success the decorator removes it once the files are stored."""
    directory = scratch_dir(name)
    try:
        yield directory
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


@contextlib.contextmanager
def work_dir(name: str) -> Iterator[Path]:
    """A scratch directory for files a step only needs while it runs."""
    directory = scratch_dir(name)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def discard_scratch(outputs: list[ledger.OutputFile]) -> None:
    root = data_root() / "scratch"
    for parent in {output.path.parent for output in outputs}:
        if parent.is_relative_to(root) and parent != root:
            shutil.rmtree(parent, ignore_errors=True)


# --- workspace artifacts ------------------------------------------------------


async def workspace_artifact(ctx: RunContext, ref: str) -> dict:
    """The artifact a label or id names in this workspace; refused when there is none."""
    artifact = await workspaces.resolve(ctx.deps.scope.key, ref)
    if artifact is None:
        raise Refusal(f"No artifact {ref!r} in this workspace. Call list_artifacts.")
    return artifact


def _missing(row: dict) -> Refusal:
    return Refusal(f"File for {row['label']} is missing from storage")


@contextlib.contextmanager
def staged_file(row: dict) -> Iterator[Path]:
    """A local path to an artifact's file while the block runs."""
    with ledger.staged(row) as source:
        if source is None:
            raise _missing(row)
        yield source


async def stage_into(row: dict, directory: Path) -> Path:
    """The artifact's file copied into ``directory``."""
    path = await asyncio.to_thread(ledger.stage, row, directory)
    if path is None:
        raise _missing(row)
    return path


async def artifact_text(row: dict) -> str:
    with staged_file(row) as source:
        return await asyncio.to_thread(source.read_text)


def listed_title(text: str | None) -> str | None:
    """A file's title as the workspace lists it: one line, cut to the length a name may
    have, so a long title never stops the file being stored."""
    title = " ".join((text or "").split())
    if len(title) > workspaces.MAX_TITLE_LENGTH:
        title = title[: workspaces.MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return title or None
