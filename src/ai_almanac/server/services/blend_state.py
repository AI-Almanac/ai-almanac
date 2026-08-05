from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BlendStatus = Literal["collecting", "runnable", "running"]


class BlendRunSpec(BaseModel):
    """Canonical blend-training configuration attached to a chat session.

    Mirrors the benchmark ``BenchmarkRunSpec`` pattern: the LLM patches it
    through ``update_blend_config`` and the server keeps it validated. Field
    names follow the blend submission body (``BlendCreate`` / ``BlendParams``).
    """

    intent: str = ""
    name: str = ""
    obs_dataset_id: str | None = None
    obs_dataset_name: str | None = None
    region_id: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    model_names: list[str] = Field(default_factory=list)
    training_years: str = ""
    cv_holdout_years: str = ""
    forecast_years: str = ""
    true_holdout_years: str = ""
    formula_text: str = ""
    status: BlendStatus = "collecting"
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class BlendValidation(BaseModel):
    can_run: bool = False
    status: BlendStatus = "collecting"
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Stable ids of the guardrail findings behind `errors` / `warnings`, so the
    # UI and the turn log can key on the rule rather than on its wording. See
    # services.guardrails.
    finding_keys: list[str] = Field(default_factory=list)
