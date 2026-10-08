from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_almanac.server.services.blend_domain import _model_candidate
from ai_almanac.server.services.forecast_models import model_training
from ai_almanac.settings import get_packaged_forecast_models

_REGISTRY = {
    "models": [
        {
            "id": "trained",
            "display_name": "Trained",
            "training": {
                "source": "https://example.org/paper",
                "periods": [
                    {
                        "stage": "pretraining",
                        "dataset": "ERA5",
                        "start_year": 1979,
                        "end_year": 2017,
                    },
                    {
                        "stage": "fine-tuning",
                        "dataset": "HRES",
                        "start_year": 2016,
                        "end_year": 2021,
                    },
                ],
            },
        },
        {"id": "undocumented", "display_name": "Undocumented"},
        {
            "id": "backwards",
            "display_name": "Backwards",
            "training": {
                "source": "https://example.org/typo",
                "periods": [
                    {
                        "stage": "pretraining",
                        "dataset": "ERA5",
                        "start_year": 2017,
                        "end_year": 1979,
                    }
                ],
            },
        },
    ]
}


def test_linked_model_reports_its_published_training_years():
    training = model_training("trained", _REGISTRY)

    assert training is not None
    assert training.source == "https://example.org/paper"
    assert [(p.stage, p.dataset, p.start_year, p.end_year) for p in training.periods] == [
        ("pretraining", "ERA5", 1979, 2017),
        ("fine-tuning", "HRES", 2016, 2021),
    ]


def test_unknown_training_years_stay_unknown():
    assert model_training("undocumented", _REGISTRY) is None
    assert model_training("not-in-registry", _REGISTRY) is None
    assert model_training(None, _REGISTRY) is None


def test_reversed_years_are_rejected():
    with pytest.raises(ValidationError):
        model_training("backwards", _REGISTRY)


def test_every_packaged_training_history_parses():
    registry = get_packaged_forecast_models()

    for entry in registry["models"]:
        model_training(entry["id"], registry)


def test_blend_candidates_carry_the_linked_models_training_years():
    linked = _model_candidate(
        {"id": "src-1", "name": "FuXi India", "metadata": {"forecast_model_id": "fuxi"}}
    )
    unlinked = _model_candidate({"id": "src-2", "name": "Pangu", "metadata": {}})

    assert linked["training"]["periods"][-1]["end_year"] == 2015
    assert unlinked["training"] is None
