"""Reading what a workspace holds: its artifacts, their lineage, and the primitives."""

from __future__ import annotations

from pydantic_ai import RunContext

from .. import workspaces
from .registry import REGISTRY, primitive, workspace_artifact


@primitive("inspect.list_artifacts", "1.0.0")
async def list_artifacts(ctx: RunContext) -> dict:
    """List every artifact in the workspace with its kind, shape summary and the chain of
    primitives that produced it. Call this first; reuse an artifact whose breadcrumb shows it
    already answers the question instead of recomputing it."""
    # Not "artifacts": chat history strips that key, which carries rendered file bytes.
    return {"catalog": await workspaces.listing(ctx.deps.scope.key)}


@primitive("inspect.inspect_artifact", "1.0.0")
async def inspect_artifact(ctx: RunContext, artifact: str) -> dict:
    """Describe one artifact by label or id: dimensions, coordinate ranges, variables and
    units for datasets; pixel size for figures. Never returns data values."""
    row = await workspace_artifact(ctx, artifact)
    return {
        "id": row["id"],
        "label": row["label"],
        "kind": row["kind"],
        "format": row["format"],
        "size_bytes": row["size_bytes"],
        "content_hash": row["content_hash"],
        "summary": row["summary"],
        "_inputs": [("artifact", row)],
    }


@primitive("inspect.describe_provenance", "1.0.0")
async def describe_provenance(ctx: RunContext, artifact: str) -> dict:
    """Render the full lineage of an artifact: every step back to its source files, with
    primitive versions, parameters and the chat turn that proposed each one."""
    row = await workspace_artifact(ctx, artifact)
    described = await workspaces.describe(ctx.deps.scope.key, row)
    return {"lineage": described["lineage"], "steps": _public_steps(described["steps"])}


@primitive("inspect.verify_provenance", "1.0.0")
async def verify_provenance(ctx: RunContext, artifact: str) -> dict:
    """Re-hash the files behind an artifact's chain and recompute every step seal. Reports
    verified, input_missing or hash_mismatch per step."""
    row = await workspace_artifact(ctx, artifact)
    described = await workspaces.describe(ctx.deps.scope.key, row, verify=True)
    return {
        "lineage": described["lineage"],
        "verification": {step["id"]: step["verification"] for step in described["steps"]},
    }


_PUBLIC_STEP_KEYS = (
    "id",
    "primitive",
    "primitive_version",
    "params",
    "inputs",
    "outputs",
    "runtime",
    "job_id",
    "turn_id",
    "model_name",
    "approval_id",
    "step_hash",
    "created_at",
)


def _public_steps(steps: list[dict]) -> list[dict]:
    return [{key: step.get(key) for key in _PUBLIC_STEP_KEYS} for step in steps]


@primitive("inspect.list_capabilities", "1.0.0")
async def list_capabilities(ctx: RunContext) -> dict:
    """List the workspace primitives with their versions, whether each needs approval, and
    whether this deployment can run it right now."""
    return {
        "capabilities": [
            {
                "name": spec.name,
                "version": spec.version,
                "needs_approval": spec.needs_approval,
                "available": spec.available(),
                "description": spec.doc.split("\n")[0],
            }
            for spec in REGISTRY.values()
        ]
    }
