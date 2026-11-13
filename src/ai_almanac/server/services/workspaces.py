"""Workspaces: per-user projects that view the user's provenance ledger (D1).

A workspace owns no provenance. ``workspace_items`` is the set of artifacts and
jobs a project has pulled in, with the labels the model uses as handles there.
Adding an artifact or job to a second workspace is a new item, never a copy or
a new step; deleting items never touches the ledger (``services/ledger.py``).
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from ai_almanac.server import tables
from ai_almanac.server.db import get_db

from . import job_access, job_history, ledger, provenance, run_summaries, workspace_display

WORKSPACE_KIND = "workspace"
NOT_IN_CONVERSATION = "This workspace is not available in this conversation."

# The most runs offered for adding to a workspace at once, newest first.
ADDABLE_JOBS_LIMIT = 200
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ARTIFACT_ITEM = "artifact"
JOB_ITEM = "job"
ADDED_BY_USER = "user"
ADDED_BY_ASSISTANT = "assistant"
# The kinds of job a workspace lists as a run.
RUN_JOB_TYPES = ("benchmark", "blend", "forecast")


class WorkspaceError(ValueError):
    """User-facing failure in a workspace operation."""


class UnreadableJob(WorkspaceError):
    """A job the workspace's owner may not read, so the workspace may not hold it."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _rows(result) -> list[dict]:
    return [dict(row) for row in result.mappings().fetchall()]


def safe_label(candidate: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", candidate).strip("._-") or "artifact"
    return cleaned[:64]


def job_file_label(filename: str) -> str:
    return safe_label(Path(filename).stem.lower())


# --- workspaces ---------------------------------------------------------------


async def create_workspace(user_id: str, name: str, description: str = "") -> dict:
    name = name.strip()
    if not name:
        raise WorkspaceError("Workspace name is required")
    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": name[:120],
        "description": description.strip()[:2000],
        "created_at": _now(),
        "updated_at": _now(),
    }
    async with get_db() as conn:
        await conn.execute(tables.workspaces.insert().values(**row))
    return row


async def list_workspaces(user_id: str) -> list[dict]:
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(tables.workspaces)
            .where(tables.workspaces.c.user_id == user_id)
            .order_by(tables.workspaces.c.updated_at.desc())
        )
        return _rows(result)


async def get_workspace(workspace_id: str, user_id: str) -> dict | None:
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(tables.workspaces).where(
                tables.workspaces.c.id == workspace_id, tables.workspaces.c.user_id == user_id
            )
        )
        rows = _rows(result)
    return rows[0] if rows else None


async def require_workspace(workspace_id: str, user_id: str) -> dict:
    workspace = await get_workspace(workspace_id, user_id)
    if workspace is None:
        raise WorkspaceError("Workspace not found")
    return workspace


async def touch(workspace_id: str) -> None:
    async with get_db() as conn:
        await conn.execute(
            tables.workspaces.update()
            .where(tables.workspaces.c.id == workspace_id)
            .values(updated_at=_now())
        )


# --- artifact items -----------------------------------------------------------


async def _labels(conn, workspace_id: str) -> dict[str, str]:
    """artifact_id → label for every artifact item in the workspace."""
    items = tables.workspace_items
    result = await conn.execute(
        sa.select(items.c.artifact_id, items.c.label).where(
            items.c.workspace_id == workspace_id, items.c.item_kind == ARTIFACT_ITEM
        )
    )
    return dict(result.fetchall())


async def labels(workspace_id: str) -> dict[str, str]:
    async with get_db() as conn:
        return await _labels(conn, workspace_id)


def _free_label(label: str, taken: set[str]) -> str:
    if label not in taken:
        return label
    for index in range(2, 1000):
        candidate = f"{label[:60]}_{index}"
        if candidate not in taken:
            return candidate
    raise WorkspaceError(f"Too many artifacts labelled {label}")


@dataclass(frozen=True)
class NewItem:
    """How a file enters a workspace: its handle, and optionally its name and whether
    it supports a result rather than being one."""

    label: str
    title: str | None = None
    supporting: bool = False


def _new_item(entry: str | NewItem) -> NewItem:
    return entry if isinstance(entry, NewItem) else NewItem(entry)


async def add_artifacts(
    workspace_id: str, entries: Sequence[tuple[dict, str | NewItem]]
) -> list[str]:
    """Pull artifacts into the workspace; returns each one's label, in order.

    An artifact already in the workspace keeps the label, name and role it has.
    """
    items = [(artifact, _new_item(entry)) for artifact, entry in entries]
    for _, item in items:
        if not LABEL_RE.match(item.label):
            raise WorkspaceError(f"Invalid artifact label: {item.label!r}")
    async with get_db() as conn:
        existing = await _labels(conn, workspace_id)
        taken = set(existing.values())
        for artifact, item in items:
            if artifact["id"] in existing:
                continue
            chosen = _free_label(item.label, taken)
            await conn.execute(
                tables.workspace_items.insert().values(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    item_kind=ARTIFACT_ITEM,
                    artifact_id=artifact["id"],
                    label=chosen,
                    title=parse_title(item.title) if item.title else None,
                    supporting=item.supporting,
                    added_at=_now(),
                )
            )
            existing[artifact["id"]] = chosen
            taken.add(chosen)
    await touch(workspace_id)
    return [existing[artifact["id"]] for artifact, _ in items]


# A name the user reads: one line, short enough for a results row.
MAX_TITLE_LENGTH = 80


def parse_title(text: str) -> str:
    title = " ".join(text.split()).strip(" \"'`")
    if not title:
        raise WorkspaceError("A name cannot be empty")
    if len(title) > MAX_TITLE_LENGTH:
        raise WorkspaceError(f"Keep names to {MAX_TITLE_LENGTH} characters")
    return title


async def rename_artifact(workspace_id: str, artifact_id: str, title: str | None) -> bool:
    """Name a file in this workspace; None goes back to the name from its filename."""
    items = tables.workspace_items
    async with get_db() as conn:
        result = await conn.execute(
            items.update()
            .where(items.c.workspace_id == workspace_id, items.c.artifact_id == artifact_id)
            .values(title=parse_title(title) if title is not None else None)
        )
    return result.rowcount > 0


def _item_columns() -> list:
    items = tables.workspace_items
    return [
        items.c.label,
        items.c.added_at,
        items.c.title.label("item_title"),
        items.c.supporting.label("supporting"),
    ]


async def resolve(workspace_id: str, ref: str) -> dict | None:
    """An artifact in this workspace, by label or artifact id, with its ``label``."""
    items, artifacts = tables.workspace_items, tables.artifacts
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(artifacts, *_item_columns())
            .join(items, items.c.artifact_id == artifacts.c.id)
            .where(
                items.c.workspace_id == workspace_id,
                sa.or_(items.c.label == ref, artifacts.c.id == ref),
            )
        )
        rows = _rows(result)
    return ledger.artifact_row(rows[0]) if rows else None


async def list_artifacts(workspace_id: str) -> list[dict]:
    """The workspace's artifacts, each with its ``label`` and ``added_at``, oldest first."""
    items, artifacts = tables.workspace_items, tables.artifacts
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(artifacts, *_item_columns())
            .join(items, items.c.artifact_id == artifacts.c.id)
            .where(items.c.workspace_id == workspace_id)
            .order_by(items.c.added_at)
        )
        return [ledger.artifact_row(row) for row in _rows(result)]


# --- job items ----------------------------------------------------------------


def linked_job_ids_query(workspace_id_param: sa.BindParameter) -> sa.Select:
    """Subquery of job ids linked to a workspace, for scoping job reads."""
    items = tables.workspace_items
    return sa.select(items.c.job_id).where(
        items.c.workspace_id == workspace_id_param, items.c.item_kind == JOB_ITEM
    )


@dataclass(frozen=True)
class _WorkspaceOwner:
    """Who a workspace reads jobs as: its owner, never with admin rights, so what it
    holds does not depend on who happens to be an admin."""

    id: str
    is_admin: bool = False


def _may_hold(job: dict, owner_id: str) -> bool:
    """Whether a workspace owned by ``owner_id`` may hold ``job`` and the files it made."""
    return job_access.can_read(job, _WorkspaceOwner(owner_id))


async def _require_holdable(conn, workspace_id: str, job_id: str) -> None:
    workspaces, jobs = tables.workspaces, tables.jobs
    owner_id = (
        await conn.execute(sa.select(workspaces.c.user_id).where(workspaces.c.id == workspace_id))
    ).scalar()
    job = (
        (
            await conn.execute(
                sa.select(jobs.c.user_id, jobs.c.visibility).where(jobs.c.id == job_id)
            )
        )
        .mappings()
        .first()
    )
    if owner_id is None or job is None or not _may_hold(dict(job), owner_id):
        raise UnreadableJob("Run not found")


async def link_job(workspace_id: str, job_id: str, *, added_by: str | None = None) -> None:
    """Pull a job into the workspace, and its outputs if it has published.

    ``added_by`` records who added an existing run: a person by hand, or the assistant
    when asked to. A run made in the workspace leaves it unset. A job already here keeps
    what was recorded first. Raises UnreadableJob for a job the owner may not read.
    """
    items = tables.workspace_items
    async with get_db() as conn:
        await _require_holdable(conn, workspace_id, job_id)
        exists = (
            await conn.execute(
                sa.select(items.c.id).where(
                    items.c.workspace_id == workspace_id, items.c.job_id == job_id
                )
            )
        ).first()
        if exists is None:
            await conn.execute(
                items.insert().values(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    item_kind=JOB_ITEM,
                    job_id=job_id,
                    added_at=_now(),
                    added_by=added_by,
                )
            )
    await _add_job_outputs(workspace_id, await ledger.complete_job_step(job_id))


async def added_runs(workspace_id: str) -> dict[str, dict]:
    """job id → when an existing run was added to the workspace and by whom (``user``
    or ``assistant``). A run made in the workspace is not among them."""
    items = tables.workspace_items
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(items.c.job_id, items.c.added_at, items.c.added_by).where(
                items.c.workspace_id == workspace_id,
                items.c.item_kind == JOB_ITEM,
                items.c.added_by.in_((ADDED_BY_USER, ADDED_BY_ASSISTANT)),
            )
        )
        return {
            job_id: {"added_at": added_at, "added_by": added_by}
            for job_id, added_at, added_by in result.fetchall()
        }


async def _add_job_outputs(workspace_id: str, outputs: list[tuple[dict, str]]) -> None:
    if outputs:
        await add_artifacts(
            workspace_id, [(artifact, job_file_label(name)) for artifact, name in outputs]
        )
    else:
        await touch(workspace_id)


async def on_job_published(job_id: str) -> None:
    """Attach a published job's outputs to its step and show them in every linked workspace."""
    outputs = await ledger.complete_job_step(job_id)
    if not outputs:
        return
    items = tables.workspace_items
    async with get_db() as conn:
        workspace_ids = (
            await conn.execute(sa.select(items.c.workspace_id).where(items.c.job_id == job_id))
        ).scalars()
        workspace_ids = list(workspace_ids)
    for workspace_id in workspace_ids:
        await _add_job_outputs(workspace_id, outputs)


async def release_unreadable_job(job: dict) -> None:
    """Take a job, and the files it published, out of every workspace whose owner may no
    longer read it, as when its owner stops sharing it."""
    items, artifacts, workspaces = tables.workspace_items, tables.artifacts, tables.workspaces
    job_outputs = sa.select(artifacts.c.id).where(artifacts.c.job_id == job["id"])
    holds_job = sa.or_(items.c.job_id == job["id"], items.c.artifact_id.in_(job_outputs))
    async with get_db() as conn:
        holders = (
            await conn.execute(
                sa.select(workspaces.c.id, workspaces.c.user_id)
                .join(items, items.c.workspace_id == workspaces.c.id)
                .where(holds_job)
                .distinct()
            )
        ).fetchall()
        released = [
            workspace_id for workspace_id, owner_id in holders if not _may_hold(job, owner_id)
        ]
        if released:
            await conn.execute(items.delete().where(items.c.workspace_id.in_(released), holds_job))


def in_workspace(workspace_id: str) -> sa.ColumnElement[bool]:
    """The jobs linked to a workspace, as a ``job_rows`` filter."""
    return tables.jobs.c.id.in_(linked_job_ids_query(sa.literal(workspace_id)))


_JOB_COLUMNS = (
    "id",
    "job_type",
    "status",
    "dataset_id",
    "run_id",
    "config_json",
    "user_id",
    "created_at",
    "started_at",
    "completed_at",
    "error",
)


async def job_rows(
    where: sa.ColumnElement[bool],
    *,
    extra: Sequence[sa.ColumnElement] = (),
    limit: int | None = None,
    oldest_first: bool = False,
) -> list[dict]:
    """Job rows matching ``where``, newest first unless ``oldest_first``, each with its
    stored settings parsed as ``config``; ``extra`` columns are read beside the usual ones."""
    jobs = tables.jobs
    order = jobs.c.created_at.asc() if oldest_first else jobs.c.created_at.desc()
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(*(jobs.c[name] for name in _JOB_COLUMNS), *extra)
            .where(where)
            .order_by(order)
            .limit(limit)
        )
        rows = _rows(result)
    for row in rows:
        row["config"] = tables.json_col(row.pop("config_json"), {})
    return rows


async def list_jobs(workspace_id: str) -> list[dict]:
    return await _listed_jobs(in_workspace(workspace_id))


async def list_addable_jobs(workspace_id: str, user_id: str) -> list[dict]:
    """The user's own runs that are not in the workspace yet, newest first: at most
    ``ADDABLE_JOBS_LIMIT`` of them, since a picker has no use for more and each run's
    stored settings are read to name it."""
    jobs = tables.jobs
    return await _listed_jobs(
        sa.and_(
            jobs.c.user_id == user_id,
            jobs.c.job_type.in_(RUN_JOB_TYPES),
            jobs.c.id.not_in(linked_job_ids_query(sa.literal(workspace_id))),
        ),
        limit=ADDABLE_JOBS_LIMIT,
    )


async def _listed_jobs(where: sa.ColumnElement[bool], limit: int | None = None) -> list[dict]:
    rows = await job_rows(where, limit=limit)
    history = await job_history.finished_jobs_of(
        _history_owner(row) for row in rows if _is_waiting(row)
    )
    return [_job_listing(row, history.get(_history_owner(row), [])) for row in rows]


def _history_owner(job: dict) -> job_history.Owner:
    return (job["user_id"], job["job_type"])


def _is_waiting(job: dict) -> bool:
    return job["status"] in run_summaries.WAITING_STATUSES


def _job_listing(row: dict, history: list[dict]) -> dict:
    """A job as a workspace lists it: what it is, what tells it from its neighbours, and
    for one still to finish, how long a run like it has taken this user before."""
    config = row["config"]
    listed = {key: value for key, value in row.items() if key not in ("config", "user_id")}
    return {
        **listed,
        "model_name": workspace_display.job_name(config),
        "scope": workspace_display.run_scope(config),
        "region_id": config.get("region_id"),
        "blended": row["job_type"] == "forecast" and bool(config.get("blend_id")),
        "area_of_interest": run_summaries.area_of_interest(config),
        "onset_definition": run_summaries.onset_definition(config),
        "expected_seconds": run_summaries.expected_seconds(row, history),
    }


# --- provenance reads ---------------------------------------------------------


def _names(artifacts: dict[str, dict], by_id: dict[str, str]) -> dict[str, str]:
    """The handle shown for each artifact: its label here, else its filename."""
    return {aid: by_id.get(aid, art["filename"]) for aid, art in artifacts.items()}


async def describe(workspace_id: str, artifact: dict, *, verify: bool = False) -> dict:
    steps, artifacts = await ledger.chain(artifact)
    status = await ledger.verify(steps, artifacts) if verify else {}
    artifacts = {**artifacts, **await _other_outputs(artifact["user_id"], steps, artifacts)}
    names = _names(artifacts, await labels(workspace_id))
    ordered = sorted(steps.values(), key=lambda s: s["created_at"])
    approved_at = await approval_times({s["approval_id"] for s in ordered if s["approval_id"]})
    added = await added_runs(workspace_id)
    return {
        "artifact": {**artifact, "label": names[artifact["id"]]},
        "steps": [
            {
                **presented_step(step),
                "verification": status.get(step["id"]),
                "approved_at": approved_at.get(step["approval_id"]),
                "added_at": added.get(step["job_id"], {}).get("added_at"),
                "added_by": added.get(step["job_id"], {}).get("added_by"),
            }
            for step in ordered
        ],
        "artifacts": [{**art, "label": names[aid]} for aid, art in artifacts.items()],
        "producers": [step["id"] for step in ledger.producers(steps, artifact["id"])],
        "lineage": provenance.render_lineage(artifact, steps, artifacts, names, status),
    }


async def _other_outputs(
    user_id: str, steps: dict[str, dict], known: dict[str, dict]
) -> dict[str, dict]:
    """Files the chain's steps made beside the ones it reads, e.g. the code a run_code step ran."""
    ids = {aid for step in steps.values() for aid in step["outputs"]} - set(known)
    return await ledger.get_artifacts(user_id, ids)


async def approval_times(approval_ids: set[str]) -> dict[str, str]:
    """approval id → when the user approved it, in one read."""
    if not approval_ids:
        return {}
    approvals = tables.approvals
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(approvals.c.id, approvals.c.created_at).where(
                approvals.c.id.in_(approval_ids)
            )
        )
        return dict(result.fetchall())


def presented_step(step: dict) -> dict:
    return {
        **step,
        "description": workspace_display.describe_step(step),
        "display_params": workspace_display.display_params(step["params"]),
    }


def breadcrumb(artifact: dict, steps: dict[str, dict]) -> list[str]:
    """Last few primitives that led to an artifact, oldest first."""
    crumbs: list[str] = []
    step = steps.get(artifact["provenance_step_id"])
    while step is not None and len(crumbs) < 3:
        crumbs.append(step["primitive"])
        step = next((steps[pid] for pid in step["parent_step_ids"] if pid in steps), None)
    return list(reversed(crumbs))


async def listing(workspace_id: str, only: set[str] | None = None) -> list[dict]:
    """Artifacts with breadcrumbs, for the tool result, the prompt and the UI."""
    rows = await list_artifacts(workspace_id)
    if not rows:
        return []
    steps = await ledger.user_steps(rows[0]["user_id"])
    index = producer_index(steps)
    return [listing_item(art, steps, index) for art in rows if only is None or art["id"] in only]


def producer_index(steps: dict[str, dict]) -> dict[str, list[dict]]:
    """artifact id → every step that output its bytes."""
    index: dict[str, list[dict]] = {}
    for step in steps.values():
        for artifact_id in step["outputs"]:
            index.setdefault(artifact_id, []).append(step)
    return index


def run_job_ids(artifact_id: str, index: dict[str, list[dict]]) -> list[str]:
    """The jobs an artifact came from: its nearest job steps upstream, oldest first.

    The walk stops at a job step, so a blend's outputs belong to the blend run
    and not also to the benchmark runs the blend read.
    """
    jobs: dict[str, str] = {}
    seen: set[str] = set()
    frontier = [artifact_id]
    while frontier:
        for step in index.get(frontier.pop(), []):
            if step["id"] in seen:
                continue
            seen.add(step["id"])
            if step["job_id"]:
                jobs[step["job_id"]] = step["created_at"]
            else:
                frontier.extend(item["artifact_id"] for item in step["inputs"])
    return sorted(jobs, key=lambda job_id: jobs[job_id])


def listing_item(
    artifact: dict, steps: dict[str, dict], index: dict[str, list[dict]] | None = None
) -> dict:
    step = steps.get(artifact["provenance_step_id"])
    return {
        "id": artifact["id"],
        "label": artifact["label"],
        "title": artifact.get("item_title") or workspace_display.artifact_title(artifact, step),
        "supporting": bool(artifact.get("supporting"))
        or workspace_display.is_training_internal(artifact["filename"]),
        "group": workspace_display.artifact_group(artifact),
        "kind": artifact["kind"],
        "format": artifact["format"],
        "created_at": artifact["added_at"],
        "job_id": step["job_id"] if step else None,
        "step_id": step["id"] if step else None,
        "turn_id": step["turn_id"] if step else None,
        "run_job_ids": run_job_ids(artifact["id"], index or producer_index(steps)),
        "breadcrumb": breadcrumb(artifact, steps),
        "summary": artifact["summary"],
    }


DIGEST_RECENT_ITEMS = 20


def prompt_listing(items: list[dict], referenced: set[str] | None = None) -> str:
    """The per-turn artifact digest, capped so the prompt does not grow with the
    workspace (D-R9): counts per group, the most recent items and anything recent
    turns read, then a pointer to ``list_artifacts`` for the rest."""
    if not items:
        return "\n\nThe workspace has no artifacts yet."
    referenced = referenced or set()
    newest = sorted(items, key=lambda item: item["created_at"])[-DIGEST_RECENT_ITEMS:]
    keep = {item["id"] for item in newest} | referenced
    shown = [item for item in items if item["id"] in keep]
    counts = Counter(item["group"] for item in items)
    lines = [
        f"\n\nThis workspace holds {len(items)} artifacts ("
        + ", ".join(f"{group}: {count}" for group, count in sorted(counts.items()))
        + "). The most recent, and those read in the last few turns "
        "(label — title — kind — how it was made):"
    ]
    for item in shown:
        crumbs = " → ".join(item["breadcrumb"]) or "unknown"
        lines.append(f"- {item['label']} — {item['title']} — {item['kind']} — {crumbs}")
    if hidden := len(items) - len(shown):
        lines.append(f"- {hidden} more — call list_artifacts to see them all.")
    return "\n".join(lines)


# --- live activity ------------------------------------------------------------


@dataclass(frozen=True)
class Activity:
    """The cheap-to-read state a workspace page watches for changes."""

    job_statuses: dict[str, str]
    artifact_created: dict[str, str]


async def activity(workspace_id: str) -> Activity:
    jobs, items = tables.jobs, tables.workspace_items
    async with get_db() as conn:
        job_statuses = await conn.execute(
            sa.select(jobs.c.id, jobs.c.status).where(in_workspace(workspace_id))
        )
        artifact_rows = await conn.execute(
            sa.select(items.c.artifact_id, items.c.added_at).where(
                items.c.workspace_id == workspace_id, items.c.item_kind == ARTIFACT_ITEM
            )
        )
        return Activity(dict(job_statuses.fetchall()), dict(artifact_rows.fetchall()))


def artifacts_since(current: Activity, since: str | None) -> set[str]:
    """Artifacts a client holding everything up to ``since`` may not have seen.

    Inclusive, since timestamps can tie; the client merges by id.
    """
    return {
        artifact_id
        for artifact_id, added_at in current.artifact_created.items()
        if since is None or added_at >= since
    }


@dataclass(frozen=True)
class ActivityChanges:
    """What moved between two activity snapshots: each is an event the page is sent."""

    jobs: bool
    new_artifacts: set[str]

    @property
    def any(self) -> bool:
        return self.jobs or bool(self.new_artifacts)


def activity_changes(previous: Activity, current: Activity) -> ActivityChanges:
    return ActivityChanges(
        jobs=previous.job_statuses != current.job_statuses,
        new_artifacts=set(current.artifact_created) - set(previous.artifact_created),
    )
