from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SpecStatus = Literal["collecting", "needs_confirmation", "runnable", "running"]


class BenchmarkRunSpec(BaseModel):
    intent: str = ""
    region_id: str | None = None
    region_name: str | None = None
    romp_region: str | None = None
    event_type: str = "monsoon_onset"
    dataset_id: str | None = None
    dataset_name: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    model_names: list[str] = Field(default_factory=list)
    forecast_window_days: int | None = 30
    status: SpecStatus = "collecting"
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    # Shared ROMP params live at the top level. Per-model ROMP params live under
    # advanced_params["per_model_params"][model_id].
    advanced_params: dict[str, Any] = Field(default_factory=dict)


class BenchmarkValidation(BaseModel):
    can_run: bool = False
    status: SpecStatus = "collecting"
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BenchmarkScope(BaseModel):
    kind: Literal["benchmark_setup", "benchmark_run_group", "job_set"] = (
        "benchmark_run_group"
    )
    key: str
    title: str | None = None
    job_ids: list[str] = Field(default_factory=list)
