"""Cancelling a run from a conversation.

The assistant's side of ``POST /jobs/{id}/cancel``: the same permission rule
(``job_access.can_modify``) and the same service call (``job_manager.signal_cancel``),
narrowed to runs in the conversation's scope. A conversation that names no runs of its
own (a setup page's chat before anything is submitted from it) can cancel nothing.

Run names, logs and error text reach the model, so text planted in one could ask for a
cancellation, and a stopped run cannot be resumed. The assistant therefore only
proposes: ``plan_cancel`` says which runs would stop, the user sees them on an approval
card (``approval_facts``), and ``cancel_run`` acts once they approve.

Results name runs as the user knows them and carry no job ids, so nothing here can
leak one into a reply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import sqlalchemy as sa

from ai_almanac.server import tables
from ai_almanac.server.db import get_db

from . import chat_tools, job_access
from .approval_display import Fact, facts
from .chat_job_events import prompt_safe
from .chat_state import ChatScope
from .job_manager import signal_cancel
from .provenance import params_hash
from .workspace_display import job_name

CANCELABLE_STATUSES = ("queued", "starting", "running")
NOT_FOUND = (
    "That run is not one of the user's runs in this conversation. "
    "Look it up in the run list and try again."
)
NOTHING_RUNNING = "Nothing to cancel: no run here is queued or running."
RUNS_MOVED = (
    "Nothing was stopped: the runs this would stop changed after the card was shown. "
    "Look at the run list again and propose the cancellation afresh."
)
# The kinds of conversation whose scope names its runs without listing their ids.
_SCOPES_HOLDING_RUNS = ("workspace", "benchmark_run_group")


@dataclass(frozen=True)
class _ChatUser:
    """Whoever the assistant acts for. A conversation never carries admin rights."""

    id: str
    is_admin: bool = False


@dataclass(frozen=True)
class CancelPlan:
    """What a cancellation would touch: the job asked about and, for a whole run, the
    other jobs submitted with it."""

    jobs: list[dict]
    whole_run: bool
    # How many jobs the run has in this conversation, whether or not they are covered.
    run_size: int

    @property
    def to_stop(self) -> list[dict]:
        return [job for job in self.jobs if job["status"] in CANCELABLE_STATUSES]


def names_its_runs(scope: ChatScope) -> bool:
    """Whether a conversation's scope says which runs are its own. One that does not
    would otherwise reach every run its user owns."""
    return scope.kind in _SCOPES_HOLDING_RUNS or bool(scope.job_ids)


async def plan_cancel(
    job_id: str, user_id: str, scope: ChatScope, *, whole_run: bool = True
) -> CancelPlan | None:
    """The jobs cancelling ``job_id`` would cover; None when it is not a run the user
    may stop from this conversation. Changes nothing."""
    job = await _modifiable_job_in_scope(job_id, user_id, scope)
    if job is None:
        return None
    siblings = await _siblings_in_scope(job, user_id, scope)
    return CancelPlan([job, *(siblings if whole_run else [])], whole_run, 1 + len(siblings))


async def cancel_run(
    job_id: str, user_id: str, scope: ChatScope, *, whole_run: bool = True
) -> dict:
    """Cancel a job in ``scope``, with the rest of its run unless ``whole_run`` is off."""
    plan = await plan_cancel(job_id, user_id, scope, whole_run=whole_run)
    if plan is None:
        return {"error": NOT_FOUND}
    return await carry_out(plan)


async def carry_out(plan: CancelPlan) -> dict:
    """Stop the runs a plan covers."""
    canceled = [await signal_cancel(each["id"]) for each in plan.to_stop]
    return cancellation_report([row for row in canceled if row], plan.jobs)


def approval_params(plan: CancelPlan) -> dict:
    """What approving a cancellation covers: exactly the runs its card says will stop.
    Hashed, because the card travels to the client and job ids never do."""
    return {"stops": params_hash({"job_ids": sorted(job["id"] for job in plan.to_stop)})}


def _names(jobs: list[dict]) -> str:
    return "; ".join(_described(job)["name"] for job in jobs)


def _coverage(plan: CancelPlan) -> str:
    """Whether the card stops one model's run or every run submitted with it."""
    if plan.run_size == 1:
        return "This run only; nothing else was submitted with it"
    if plan.whole_run:
        return f"The whole run: all {plan.run_size} of the runs submitted together"
    others = plan.run_size - 1
    kept = "the other run" if others == 1 else f"the other {others} runs"
    return f"This one run only; {kept} submitted with it will keep going"


def approval_facts(plan: CancelPlan) -> list[Fact]:
    """What the approval card says a cancellation will do, naming each run it stops."""
    stopping = {job["id"] for job in plan.to_stop}
    return facts(
        ("Stop", _names(plan.to_stop)),
        ("Covers", _coverage(plan)),
        ("Already ended", _names([job for job in plan.jobs if job["id"] not in stopping])),
        ("Results", "Results already written are kept. A stopped run cannot be resumed."),
    )


def cancellation_report(canceled: list[dict], considered: list[dict]) -> dict:
    """What the assistant is told: which runs are stopping and which had nothing to stop."""
    stopping = {row["id"] for row in canceled}
    return {
        "canceled": [_described(row) for row in canceled],
        "not_running": [_described(row) for row in considered if row["id"] not in stopping],
        "message": _summary(len(canceled)),
    }


def _summary(count: int) -> str:
    if count == 0:
        return NOTHING_RUNNING
    runs = "1 run" if count == 1 else f"{count} runs"
    return f"Canceling {runs}. Results already written are kept."


def _described(job: dict) -> dict:
    job_type = job.get("job_type")
    return {
        "name": prompt_safe(job_name(json.loads(job.get("config_json") or "{}")) or ""),
        "kind": job_type if job_type in ("blend", "forecast") else "benchmark",
        "status": job["status"],
    }


async def _modifiable_job_in_scope(job_id: str, user_id: str, scope: ChatScope) -> dict | None:
    if not names_its_runs(scope):
        return None
    visible = await chat_tools.get_job_info(job_id, user_id, scope)
    if visible.get("error"):
        return None
    job = await job_access.fetch_job(job_id)
    return job if job and job_access.can_modify(job, _ChatUser(user_id)) else None


async def _siblings_in_scope(job: dict, user_id: str, scope: ChatScope) -> list[dict]:
    """The other jobs of the same run: a benchmark is one job per model under one run."""
    if not job.get("run_id"):
        return []
    jobs = tables.jobs
    async with get_db() as conn:
        sibling_ids = list(
            (
                await conn.execute(
                    sa.select(jobs.c.id)
                    # run_id is client-supplied, so siblings are the owner's jobs only.
                    .where(
                        jobs.c.run_id == job["run_id"],
                        jobs.c.user_id == job["user_id"],
                        jobs.c.id != job["id"],
                    )
                    .order_by(jobs.c.created_at)
                )
            ).scalars()
        )
    siblings = [await _modifiable_job_in_scope(each, user_id, scope) for each in sibling_ids]
    return [sibling for sibling in siblings if sibling]
