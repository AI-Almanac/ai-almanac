"""What a turn read (D-R8): the workspace artifacts behind its data-reading tool results.

Every data-reading tool names the files it read as ``source_artifacts: [label]``.
Labels are what the model and the user see; the turn log records artifact ids, so
the chat loop resolves the labels once per result. Outside a workspace there are
no labels, so tools omit the field and a turn's read set stays empty.
"""

from __future__ import annotations

from collections.abc import Callable

from . import skill_files
from .benchmark_state import BenchmarkScope

WORKSPACE_KIND = "workspace"
FileFilter = Callable[[str], bool]


def is_spatial_metrics_file(filename: str) -> bool:
    return skill_files.is_skill_file(filename)


def is_file_named(*names: str) -> FileFilter:
    return frozenset(names).__contains__


async def job_file_labels(workspace_id: str, job_id: str, reads: FileFilter) -> list[str]:
    """Labels, in this workspace, of the job's output files that ``reads`` accepts."""
    from . import ledger, workspaces

    step = await ledger.job_step(job_id)
    if step is None:
        return []
    by_id = await workspaces.labels(workspace_id)
    artifacts = await ledger.get_artifacts(step["user_id"], set(step["outputs"]) & by_id.keys())
    return sorted(by_id[aid] for aid, art in artifacts.items() if reads(art["filename"]))


async def with_job_sources(
    result: dict, scope: BenchmarkScope, job_id: str, reads: FileFilter
) -> dict:
    """``result`` plus the workspace labels of the job files it was read from."""
    if scope.kind != WORKSPACE_KIND or result.get("error"):
        return result
    return {**result, "source_artifacts": await job_file_labels(scope.key, job_id, reads)}


def labels_of(inputs: list[tuple[str, dict]]) -> list[str]:
    """Source labels for a primitive's ``_inputs`` (role, artifact row) pairs."""
    return list(dict.fromkeys(row["label"] for _, row in inputs if row.get("label")))


async def resolve_sources(workspace_id: str, source_labels: object) -> list[dict[str, str]]:
    """``[{"id", "label"}]`` for the given labels that name artifacts in the workspace."""
    if not isinstance(source_labels, list) or not source_labels:
        return []
    from . import workspaces

    by_label = {label: aid for aid, label in (await workspaces.labels(workspace_id)).items()}
    return [
        {"id": by_label[label], "label": label}
        for label in dict.fromkeys(source_labels)
        if isinstance(label, str) and label in by_label
    ]
