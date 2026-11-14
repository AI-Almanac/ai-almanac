"""Workspaces, their conversations, and files put in them."""

from __future__ import annotations

from pathlib import Path

from ai_almanac.server.services import ledger, workspaces
from ai_almanac.server.services.storage import get_storage
from tests.helpers.files import write_nc


async def _open_conversation(client, headers, workspace_id: str) -> dict:
    scope = {"kind": "workspace", "key": workspace_id, "job_ids": []}
    response = await client.post(
        "/chat/sessions", headers=headers, json={"title": "ws", "scope": scope}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def conversation_in(client, headers, workspace_id: str) -> str:
    return (await _open_conversation(client, headers, workspace_id))["id"]


async def workspace_with_session(client, headers) -> tuple[dict, dict]:
    """A new workspace and a conversation inside it: (workspace, session)."""
    response = await client.post("/workspaces", headers=headers, json={"name": "Senegal 2026"})
    assert response.status_code == 201, response.text
    workspace = response.json()
    return workspace, await _open_conversation(client, headers, workspace["id"])


async def register_file(user_id: str, workspace_id: str, path: Path, label: str) -> dict:
    """Put a file in the ledger as a fetch step's output and show it in the workspace."""
    _, [artifact] = await ledger.write_step(
        ledger.StepDraft(primitive="fetch.test", version="1.0.0", params={"source": path.name}),
        ledger.Attribution.by_user(user_id),
        [ledger.OutputFile(path=path, label=label, kind="dataset")],
    )
    await workspaces.add_artifacts(workspace_id, [(artifact, label)])
    return artifact


async def job_step_in_record(client, headers, workspace_id: str, job_id: str) -> dict:
    """The step for ``job_id`` in the record of the metrics file that job made."""
    detail = (await client.get(f"/workspaces/{workspace_id}", headers=headers)).json()
    file = next(
        a for a in detail["artifacts"] if a["job_id"] == job_id and "spatial_metrics" in a["label"]
    )
    record = await client.get(
        f"/workspaces/{workspace_id}/artifacts/{file['id']}/provenance", headers=headers
    )
    assert record.status_code == 200, record.text
    return next(step for step in record.json()["steps"] if step["job_id"] == job_id)


def write_job_outputs(job_id: str, model_seed: float) -> None:
    """The climatology and one model's spatial metrics, as a finished benchmark leaves them."""
    output, _ = get_storage().job_output_uri(job_id)
    write_nc(Path(output) / "climatology_onset_doy_1998-2024.nc", seed=7.0)
    write_nc(Path(output) / "spatial_metrics_AIFS_1-15.nc", seed=model_seed)
