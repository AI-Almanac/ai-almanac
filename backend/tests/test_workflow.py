from __future__ import annotations

from app.routers.jobs import JobCreate, RompParams
from app.routers.workflow import _validate_compiled_jobs


def test_validate_compiled_jobs_reports_all_unknown_models(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.workflow.get_model_registry",
        lambda: [{"id": "aifs", "region": "india", "model_dir": "/models/aifs"}],
    )

    errors = _validate_compiled_jobs(
        [
            JobCreate(
                dataset_id="demo:india",
                model_name="aifs",
                params=RompParams(region="india"),
            ),
            JobCreate(
                dataset_id="demo:india",
                model_name="missing-model",
                params=RompParams(region="india"),
            ),
            JobCreate(
                dataset_id="demo:india",
                model_name="another-missing-model",
                params=RompParams(region="india"),
            ),
        ]
    )

    assert [error.message for error in errors] == [
        "Unknown model: 'missing-model'",
        "Unknown model: 'another-missing-model'",
    ]
