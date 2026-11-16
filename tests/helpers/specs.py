"""Run specs the assistant would hand to a workspace's submission tools."""

from __future__ import annotations

import uuid

from ai_almanac.server.services.primitives import runs
from tests.helpers.records import seed_source


async def india_blend_spec(true_holdout_years: str) -> runs.BlendSpec:
    """A two-model blend over freshly registered India data, trained on 2000-2019."""
    obs = await seed_source("obs", f"obs-{uuid.uuid4().hex[:8]}", years=(1980, 2024))
    models = [
        await seed_source("model", f"m{n}-{uuid.uuid4().hex[:8]}", years=(1980, 2024))
        for n in range(2)
    ]
    return runs.BlendSpec(
        name="India blend",
        obs_dataset_id=obs,
        model_ids=models,
        training_years="2000:2019",
        cv_holdout_years="2000:2019",
        true_holdout_years=true_holdout_years,
    )
