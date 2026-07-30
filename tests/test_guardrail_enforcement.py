"""Guardrail errors block on every entry point, not just the chat path.

The assistant is an untrusted user of the platform, so a statistical rule that
only holds when the model cooperates is not a rule. These tests assert the rule
holds at the submission chokepoint, which is what the REST API, the manual UI
form, and the assistant's submit_blend tool all pass through — a config the
chat path rejects must not be reachable with a direct POST.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text

from ai_almanac.server.services import job_submission


async def _seed_source(kind: str, name: str, region: str, start_year: int, end_year: int) -> str:
    from ai_almanac.server.db import get_db

    source_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, :region, :metadata, 'gcs', "
                "'ready', NULL, :now, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": f"gs://data/{kind}/{name}",
                "region": region,
                "metadata": json.dumps({"start_year": start_year, "end_year": end_year}),
                "now": now,
            },
        )
    return source_id


def _leaky_body(obs_id: str, model_ids: list[str]) -> dict:
    """A blend whose true holdout was also trained on."""
    return {
        "name": "leaky holdout",
        "obs_dataset_id": obs_id,
        "model_ids": model_ids,
        "params": {
            "training_years": "2000:2019",
            "cv_holdout_years": "2000:2019",
            "true_holdout_years": "2018,2019",
        },
    }


@pytest.mark.asyncio
async def test_post_blends_rejects_a_true_holdout_that_was_trained_on(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    obs_id = await _seed_source("obs", f"obs-{uuid.uuid4().hex[:8]}", "india", 1980, 2024)
    model_id = await _seed_source("model", f"aifs-{uuid.uuid4().hex[:8]}", "india", 1980, 2024)

    res = await client.post("/blends", json=_leaky_body(obs_id, [model_id]), headers=auth_headers)

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "holdout" in detail.lower()
    assert "2018" in detail


@pytest.mark.asyncio
async def test_a_clean_split_is_accepted_on_the_same_path(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Guards against the check rejecting everything, which would pass the test
    above for the wrong reason."""
    obs_id = await _seed_source("obs", f"obs-{uuid.uuid4().hex[:8]}", "india", 1980, 2024)
    model_id = await _seed_source("model", f"aifs-{uuid.uuid4().hex[:8]}", "india", 1980, 2024)

    body = _leaky_body(obs_id, [model_id])
    body["params"]["true_holdout_years"] = "2020,2021"

    res = await client.post("/blends", json=body, headers=auth_headers)

    assert res.status_code != 400, res.text


@pytest.mark.asyncio
async def test_submitted_blend_freezes_guardrail_warnings_with_the_job(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Warnings ride along with the result they apply to, like the existing
    live-forecast note, so the caution survives the conversation that produced
    it."""
    obs_id = await _seed_source("obs", f"obs-{uuid.uuid4().hex[:8]}", "india", 1980, 2024)
    models = [
        await _seed_source("model", f"m{n}-{uuid.uuid4().hex[:8]}", "india", 1980, 2024)
        for n in range(3)
    ]

    body = _leaky_body(obs_id, models)
    body["params"] = {
        "training_years": "2015:2018",
        "cv_holdout_years": "2015:2018",
    }

    res = await client.post("/blends", json=body, headers=auth_headers)

    assert res.status_code < 400, res.text
    warnings = " ".join(res.json()["warnings"])
    assert "3 models" in warnings
    assert "4 year" in warnings


def test_min_onset_years_is_sourced_from_the_guardrails_record() -> None:
    """One number, not four copies. The mirrors in modal/blending_app.py and
    year-coverage.ts are still by-hand; this pins the two Python readers."""
    from ai_almanac.server.services import blend_domain, guardrails

    # Ruff reads an ALL_CAPS name on the left of `==` as a Yoda condition, hence
    # the ordering here.
    expected = guardrails.DEFAULT_GUARDRAILS.min_onset_years
    assert expected == job_submission.MIN_ONSET_YEARS
    assert expected == blend_domain.MIN_ONSET_YEARS
