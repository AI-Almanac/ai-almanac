"""Fitting a chat's history into the model's context window (services.llm)."""

from __future__ import annotations

import json

from ai_almanac.server.services import llm
from ai_almanac.settings import settings


def _outline(points: int) -> dict:
    ring = [[70.0 + i / points, 10.0 + i / points] for i in range(points)]
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def _rerun_result(points: int = 30_000) -> dict:
    """The shape rerun_job returned when the job had an area of interest."""
    return {
        "source_job_id": "6cada2a6",
        "job": {
            "id": "1ba3ca50",
            "model_name": "AIFSv2",
            "status": "queued",
            "params": {
                "region": "india",
                "focus_area": {"level": "adm1", "units": ["Kerala"], "geometry": _outline(points)},
            },
        },
    }


def test_an_area_outline_never_reaches_the_model() -> None:
    raw = _rerun_result()
    assert len(json.dumps(raw)) > 500_000

    compact = llm._compact_tool_content(raw)

    focus_area = compact["job"]["params"]["focus_area"]
    assert focus_area == {"level": "adm1", "units": ["Kerala"]}
    assert len(json.dumps(compact)) < 1_000


def test_a_tool_result_is_capped_as_a_whole_not_only_its_strings() -> None:
    numbers = {"values": list(range(50_000))}

    compact = llm._compact_tool_content(numbers)

    cap = max(
        settings.llm_tool_result_max_chars * llm._TOOL_RESULT_TOTAL_FACTOR,
        llm._TOOL_RESULT_TOTAL_FLOOR,
    )
    assert isinstance(compact, str)
    assert len(compact) <= cap + len(llm._TRUNCATED_TOOL_RESULT) + 4
    assert compact.endswith(llm._TRUNCATED_TOOL_RESULT)
