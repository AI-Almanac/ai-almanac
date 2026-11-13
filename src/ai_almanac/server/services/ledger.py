"""The per-user provenance ledger: artifacts, steps and approvals (D2, D3).

One step per real execution. A primitive's step is written when it returns; a
job's step is written at submission, carrying whoever submitted it, and its
outputs are attached when the job publishes. Workspaces are views over this
ledger (``services/workspaces.py``) and hold no provenance of their own.

Only this module writes these tables. Nothing updates a step except attaching
a job's outputs, and nothing deletes one (P5). Artifact bytes go through the
storage port only (P7): ``open_bytes`` to stream, ``stage`` for a local file.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import sqlalchemy as sa

from ai_almanac import __version__
from ai_almanac.server import tables
from ai_almanac.server.db import get_db
from ai_almanac.settings import settings

from . import provenance
from .storage import get_storage

if TYPE_CHECKING:
    from .turn_log import TurnRecord

OWNED = "owned"
JOB_OUTPUT = "job_output"
JOB_STEP_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _rows(result) -> list[dict]:
    return [dict(row) for row in result.mappings().fetchall()]


def step_row(row: dict) -> dict:
    return {
        **row,
        "params": tables.json_col(row["params"], {}),
        "inputs": tables.json_col(row["inputs"], []),
        "outputs": tables.json_col(row["outputs"], []),
        "runtime": tables.json_col(row["runtime"], {}),
        "parent_step_ids": tables.json_col(row["parent_step_ids"], []),
    }


def artifact_row(row: dict) -> dict:
    return {**row, "summary": tables.json_col(row["summary"], {})}


# --- attribution and approvals ------------------------------------------------


@dataclass(frozen=True)
class Attribution:
    """Who is behind a step: the actor, and whether a human approved it."""

    user_id: str
    actor_kind: str  # assistant | user | system
    approved: bool
    session_id: str | None = None
    turn_id: str | None = None
    ruleset_id: str | None = None
    ruleset_version: int | None = None
    model_name: str | None = None
    # What the approver was shown, when it is not the job's own params (D8).
    approved_payload: dict | None = None

    @classmethod
    def by_user(cls, user_id: str) -> Attribution:
        """A REST or UI action: the caller acted directly, so it is auto-approved (D-R3)."""
        return cls(user_id, "user", approved=True)

    @classmethod
    def by_system(cls, user_id: str) -> Attribution:
        return cls(user_id, "system", approved=False)

    @classmethod
    def by_assistant(
        cls, user_id: str, session_id: str | None, *, approved: bool, turn: TurnRecord | None
    ) -> Attribution:
        """The assistant acting in a conversation; ``turn`` names the turn, ruleset and
        model that proposed the step, when it ran inside one."""
        return cls(
            user_id,
            "assistant",
            approved,
            session_id=session_id or (turn.session_id if turn else None),
            turn_id=(turn.turn_id or None) if turn else None,
            ruleset_id=turn.ruleset_id if turn else None,
            ruleset_version=turn.ruleset_version if turn else None,
            model_name=turn.model_name if turn else None,
        )


async def record_approval(attribution: Attribution, kind: str, payload: dict) -> str | None:
    """The one approval recorder for every gated action; None when nobody approved it."""
    if not attribution.approved:
        return None
    row = {
        "id": str(uuid.uuid4()),
        "user_id": attribution.user_id,
        "session_id": attribution.session_id,
        "turn_id": attribution.turn_id,
        "created_at": _now(),
        "kind": kind,
        "payload_hash": provenance.params_hash(payload),
    }
    async with get_db() as conn:
        await conn.execute(tables.approvals.insert().values(**row))
    return row["id"]


# --- storage port -------------------------------------------------------------


def owned_key(user_id: str, content_hash: str, filename: str) -> str:
    return f"{user_id}/{content_hash}{Path(filename).suffix.lower()}"


def local_path(artifact: dict) -> Path | None:
    """The stored file itself, when storage is local; None under GCS."""
    storage = get_storage()
    if artifact["location_kind"] == OWNED:
        return storage.artifact_path(artifact["path"])
    return storage.result_file_path(
        artifact["job_id"], artifact["job_file_kind"], artifact["filename"]
    )


def open_bytes(artifact: dict) -> BinaryIO | None:
    storage = get_storage()
    if artifact["location_kind"] == OWNED:
        return storage.open_artifact(artifact["path"])
    return storage.open_result(artifact["job_id"], artifact["job_file_kind"], artifact["filename"])


def stage(artifact: dict, directory: Path) -> Path | None:
    """A local file holding the artifact's bytes: the stored file when storage is
    local, else a download into ``directory``. None when the bytes are missing."""
    path = local_path(artifact)
    if path is not None:
        return path if path.is_file() else None
    handle = open_bytes(artifact)
    if handle is None:
        return None
    target = directory / Path(artifact["filename"]).name
    with handle, target.open("wb") as out:
        shutil.copyfileobj(handle, out)
    return target


@contextmanager
def staged(artifact: dict) -> Iterator[Path | None]:
    with tempfile.TemporaryDirectory(prefix="artifact-") as scratch:
        yield stage(artifact, Path(scratch))


def current_hash(artifact: dict) -> str | None:
    """sha256 of the bytes in storage now, or None if they are gone."""
    handle = open_bytes(artifact)
    if handle is None:
        return None
    with handle:
        return provenance.hash_stream(handle)


# --- writing steps ------------------------------------------------------------


@dataclass
class OutputFile:
    path: Path
    label: str  # the handle suggested to the workspace; the ledger holds no labels
    kind: str | None = None
    # How the workspace shows the file: its name there (None: derived from the
    # filename), and whether a result was made from it rather than it being one.
    title: str | None = None
    supporting: bool = False


@dataclass
class StepDraft:
    primitive: str
    version: str
    params: dict
    inputs: list[tuple[str, dict]] = field(default_factory=list)  # (role, artifact row)
    job_id: str | None = None
    code_hash: str | None = None
    runtime: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _FileFacts:
    filename: str
    kind: str
    format: str
    content_hash: str
    size_bytes: int
    summary: dict


def _facts(path: Path, filename: str, kind: str | None) -> _FileFacts:
    return _FileFacts(
        filename=filename,
        kind=kind or provenance.kind_of(filename),
        format=provenance.format_of(filename),
        content_hash=provenance.content_hash(path),
        size_bytes=path.stat().st_size,
        summary=provenance.summarize(path),
    )


async def _steps_by_id(conn, step_ids: set[str]) -> dict[str, dict]:
    if not step_ids:
        return {}
    result = await conn.execute(
        sa.select(tables.provenance_steps).where(tables.provenance_steps.c.id.in_(list(step_ids)))
    )
    return {row["id"]: step_row(row) for row in _rows(result)}


async def _new_step(
    draft: StepDraft, attribution: Attribution, approval_id: str | None, *, completed: bool
) -> dict:
    parent_ids = sorted({row["provenance_step_id"] for _, row in draft.inputs})
    async with get_db() as conn:
        parents = await _steps_by_id(conn, set(parent_ids))
    inputs = [
        {"role": role, "artifact_id": row["id"], "content_hash": row["content_hash"]}
        for role, row in draft.inputs
    ]
    digest = provenance.params_hash(draft.params)
    now = _now()
    return {
        "id": str(uuid.uuid4()),
        "user_id": attribution.user_id,
        "created_at": now,
        "completed_at": now if completed else None,
        "actor_kind": attribution.actor_kind,
        "primitive": draft.primitive,
        "primitive_version": draft.version,
        "params": draft.params,
        "params_hash": digest,
        "inputs": inputs,
        "outputs": [],
        "runtime": draft.runtime,
        "job_id": draft.job_id,
        "code_hash": draft.code_hash,
        "turn_id": attribution.turn_id,
        "session_id": attribution.session_id,
        "ruleset_id": attribution.ruleset_id,
        "ruleset_version": attribution.ruleset_version,
        "model_name": attribution.model_name,
        "approval_id": approval_id,
        "parent_step_ids": parent_ids,
        "step_hash": provenance.step_hash(
            draft.primitive,
            draft.version,
            digest,
            [(item["role"], item["content_hash"]) for item in inputs],
            provenance.runtime_digest(draft.runtime),
            [parents[pid]["step_hash"] for pid in parent_ids if pid in parents],
        ),
    }


async def _artifacts_by_hash(conn, user_id: str, hashes: set[str]) -> dict[str, dict]:
    result = await conn.execute(
        sa.select(tables.artifacts).where(
            tables.artifacts.c.user_id == user_id,
            tables.artifacts.c.content_hash.in_(list(hashes)),
        )
    )
    return {row["content_hash"]: artifact_row(row) for row in _rows(result)}


def _artifact_values(user_id: str, step_id: str, facts: _FileFacts, location: dict) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "created_at": _now(),
        "kind": facts.kind,
        "format": facts.format,
        "filename": facts.filename,
        "content_hash": facts.content_hash,
        "size_bytes": facts.size_bytes,
        "summary": facts.summary,
        "provenance_step_id": step_id,
        "path": None,
        "job_id": None,
        "job_file_kind": None,
        **location,
    }


async def _insert_new_artifacts(
    conn, user_id: str, step_id: str, located: list[tuple[_FileFacts, dict]]
) -> list[dict]:
    """One artifact row per file, reusing the user's row for bytes already known.

    ponytail: two concurrent steps writing the same new bytes race on the
    (user_id, content_hash) constraint and the second fails; retry the lookup
    on IntegrityError if that ever shows up.
    """
    known = await _artifacts_by_hash(conn, user_id, {facts.content_hash for facts, _ in located})
    rows = []
    for facts, location in located:
        if facts.content_hash not in known:
            values = _artifact_values(user_id, step_id, facts, location)
            await conn.execute(tables.artifacts.insert().values(**values))
            known[facts.content_hash] = values
        rows.append(known[facts.content_hash])
    return rows


def _unique_ids(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(row["id"] for row in rows))


async def write_step(
    draft: StepDraft,
    attribution: Attribution,
    outputs: list[OutputFile],
    approval_id: str | None = None,
) -> tuple[dict, list[dict]]:
    """Record a finished execution and store its files; returns (step, artifact per output)."""
    step = await _new_step(draft, attribution, approval_id, completed=True)
    facts = [
        await asyncio.to_thread(_facts, output.path, output.path.name, output.kind)
        for output in outputs
    ]
    storage = get_storage()
    located = []
    for output, fact in zip(outputs, facts, strict=True):
        key = owned_key(attribution.user_id, fact.content_hash, fact.filename)
        await asyncio.to_thread(storage.put_artifact, key, output.path)
        located.append((fact, {"location_kind": OWNED, "path": key}))
    async with get_db() as conn:
        artifacts = await _insert_new_artifacts(conn, attribution.user_id, step["id"], located)
        step["outputs"] = _unique_ids(artifacts)
        await conn.execute(tables.provenance_steps.insert().values(**step))
    return step, artifacts


async def open_job_step(job_id: str, job_type: str, params: dict, attribution: Attribution) -> dict:
    """Write a job's step at submission; its outputs are attached when it publishes."""
    approval_id = await record_approval(
        attribution, f"submit_{job_type}", attribution.approved_payload or params
    )
    step = await _new_step(
        StepDraft(
            primitive=f"{job_type}.job",
            version=JOB_STEP_VERSION,
            params=params,
            job_id=job_id,
            runtime={"runner": settings.job_runner, "ai_almanac": __version__},
        ),
        attribution,
        approval_id,
        completed=False,
    )
    async with get_db() as conn:
        await conn.execute(tables.provenance_steps.insert().values(**step))
    return step


async def job_step(job_id: str) -> dict | None:
    async with get_db() as conn:
        rows = _rows(
            await conn.execute(
                sa.select(tables.provenance_steps)
                .where(tables.provenance_steps.c.job_id == job_id)
                .order_by(tables.provenance_steps.c.created_at)
            )
        )
    return step_row(rows[0]) if rows else None


def _job_file_location(job_id: str, kind: str, filename: str) -> dict:
    return {
        "location_kind": JOB_OUTPUT,
        "job_id": job_id,
        "job_file_kind": kind,
        "filename": filename,
    }


def _job_file_facts(job_id: str, kind: str, filename: str, file_kind: str) -> _FileFacts | None:
    with staged(_job_file_location(job_id, kind, filename)) as path:
        return _facts(path, filename, file_kind) if path is not None else None


async def _job(job_id: str) -> dict | None:
    async with get_db() as conn:
        rows = _rows(await conn.execute(sa.select(tables.jobs).where(tables.jobs.c.id == job_id)))
    return rows[0] if rows else None


async def complete_job_step(job_id: str) -> list[tuple[dict, str]]:
    """Attach a completed job's files to its step; returns (artifact, filename) per file.

    Idempotent. Each file is hashed once, here. A job submitted before the
    ledger existed gets a ``system`` step now, with no approval on record.
    """
    job = await _job(job_id)
    if job is None or job["status"] != "complete":
        return []
    step = await job_step(job_id)
    if step is None:
        config = tables.json_col(job.get("config_json"), {})
        step = await open_job_step(
            job_id,
            job["job_type"],
            {"dataset_id": job["dataset_id"], **config},
            Attribution.by_system(job["user_id"]),
        )
    if step["completed_at"]:
        return await _step_outputs(step)

    files = await asyncio.to_thread(get_storage().list_result_files, job_id)
    netcdf_kind = "dataset" if job["job_type"] == "forecast" else "metrics"
    located, names = [], []
    for kind, filename in files:
        if "/" in filename:
            continue  # ponytail: nested files (forecast rasters) stay job-only
        file_kind = netcdf_kind if filename.endswith(".nc") else provenance.kind_of(filename)
        facts = await asyncio.to_thread(_job_file_facts, job_id, kind, filename, file_kind)
        if facts is not None:
            located.append((facts, _job_file_location(job_id, kind, filename)))
            names.append(filename)
    async with get_db() as conn:
        artifacts = await _insert_new_artifacts(conn, job["user_id"], step["id"], located)
        await conn.execute(
            tables.provenance_steps.update()
            .where(
                tables.provenance_steps.c.id == step["id"],
                tables.provenance_steps.c.completed_at.is_(None),
            )
            .values(outputs=_unique_ids(artifacts), completed_at=_now())
        )
    return list(zip(artifacts, names, strict=True))


async def _step_outputs(step: dict) -> list[tuple[dict, str]]:
    artifacts = await get_artifacts(step["user_id"], set(step["outputs"]))
    return [
        (artifacts[aid], artifacts[aid]["filename"]) for aid in step["outputs"] if aid in artifacts
    ]


# --- reading ------------------------------------------------------------------


async def get_artifacts(user_id: str, ids: set[str]) -> dict[str, dict]:
    if not ids:
        return {}
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(tables.artifacts).where(
                tables.artifacts.c.user_id == user_id, tables.artifacts.c.id.in_(list(ids))
            )
        )
        return {row["id"]: artifact_row(row) for row in _rows(result)}


async def user_steps(user_id: str) -> dict[str, dict]:
    """Every step of a user's ledger.

    ponytail: loads the whole ledger per read; walk the DAG with a recursive
    query once ledgers get large.
    """
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(tables.provenance_steps).where(tables.provenance_steps.c.user_id == user_id)
        )
        return {row["id"]: step_row(row) for row in _rows(result)}


def producers(steps: dict[str, dict], artifact_id: str) -> list[dict]:
    """Every step that output these bytes, oldest first (D-R6)."""
    return sorted(
        (step for step in steps.values() if artifact_id in step["outputs"]),
        key=lambda step: step["created_at"],
    )


async def chain(artifact: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """All steps and artifacts upstream of ``artifact``, through every producer."""
    all_steps = await user_steps(artifact["user_id"])
    steps: dict[str, dict] = {}
    artifacts: dict[str, dict] = {artifact["id"]: artifact}
    frontier = [artifact["id"]]
    while frontier:
        current = frontier.pop()
        for step in producers(all_steps, current):
            if step["id"] in steps:
                continue
            steps[step["id"]] = step
            frontier.extend(item["artifact_id"] for item in step["inputs"])
    input_ids = {item["artifact_id"] for step in steps.values() for item in step["inputs"]}
    artifacts.update(await get_artifacts(artifact["user_id"], input_ids - set(artifacts)))
    return steps, artifacts


def verify_step(
    step: dict, steps: dict[str, dict], artifacts: dict[str, dict], hashes: dict[str, str | None]
) -> str:
    """Re-hash the step's files still in storage and recompute its seal.

    ``hashes`` memoises current hashes across the steps of one chain. Blocking;
    run it in a thread.
    """

    def hash_of(artifact_id: str) -> str | None:
        if artifact_id not in hashes:
            art = artifacts.get(artifact_id)
            hashes[artifact_id] = current_hash(art) if art else None
        return hashes[artifact_id]

    for item in step["inputs"]:
        found = hash_of(item["artifact_id"])
        if found is None:
            return "input_missing"
        if found != item["content_hash"]:
            return "hash_mismatch"
    digest = provenance.params_hash(step["params"])
    recomputed = provenance.step_hash(
        step["primitive"],
        step["primitive_version"],
        digest,
        [(item["role"], item["content_hash"]) for item in step["inputs"]],
        provenance.runtime_digest(step["runtime"]),
        [steps[pid]["step_hash"] for pid in step["parent_step_ids"] if pid in steps],
    )
    if recomputed != step["step_hash"] or digest != step["params_hash"]:
        return "hash_mismatch"
    for artifact_id in step["outputs"]:
        if artifact_id not in artifacts:
            continue
        found = hash_of(artifact_id)
        if found is None:
            return "input_missing"
        if found != artifacts[artifact_id]["content_hash"]:
            return "hash_mismatch"
    return "verified"


async def verify(steps: dict[str, dict], artifacts: dict[str, dict]) -> dict[str, str]:
    def run() -> dict[str, str]:
        hashes: dict[str, str | None] = {}
        return {sid: verify_step(step, steps, artifacts, hashes) for sid, step in steps.items()}

    return await asyncio.to_thread(run)


def history(steps: dict[str, dict]) -> list[dict]:
    """The chain as stamped into a download, oldest step first."""
    return [
        provenance.history_entry(step)
        for step in sorted(steps.values(), key=lambda s: s["created_at"])
    ]


def stamped_copy(artifact: dict, chain_history: list[dict], directory: Path) -> Path | None:
    """A copy of the artifact in ``directory`` with its chain stamped in (D-R10).

    The stored file is never modified; only the copy a user downloads carries
    the chain.
    """
    handle = open_bytes(artifact)
    if handle is None:
        return None
    target = directory / Path(artifact["filename"]).name
    with handle, target.open("wb") as out:
        shutil.copyfileobj(handle, out)
    provenance.stamp_history(target, chain_history)
    return target
