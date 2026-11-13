"""What a forecast would cost before anyone pays for it.

Read-only: resolves the same blend or models, season and plan a submission
would, then reports per model whether it can run live, whether its shared
season cache already holds every issue date, and the GPU time the rest needs.
"""

from __future__ import annotations

from dataclasses import asdict

from ai_almanac.server.db import get_db
from ai_almanac.server.services import job_submission, trajectory_sets
from ai_almanac.server.services.forecast_models import live_forecast_compatibility
from ai_almanac.server.services.forecast_pipeline import SeasonPlan, estimate_rollout
from ai_almanac.server.services.job_submission import ForecastBasis, ForecastCreate
from ai_almanac.server.services.seasons import resolve_season


def _live_model(name: str, basis: ForecastBasis, registry: dict) -> dict:
    member = job_submission.blend_member(name, basis.sources_by_name.get(name) or {})
    verdict = live_forecast_compatibility(member.forecast_model_id, member.archive_step, registry)
    report = {"model": name, "live_model": verdict.model_id}
    if verdict.status != "ready":
        report["cannot_run_live"] = verdict.detail
    return report


async def _cache_reports(plan: SeasonPlan) -> dict[str, dict]:
    async with get_db() as conn:
        ready = await job_submission.season_cache_readiness(conn, plan)
        missing = {
            run.member: await trajectory_sets.missing_dates(
                conn,
                model_name=run.member,
                init_source=plan.init_source,
                season=str(plan.season),
                needed_dates=run.issue_dates,
            )
            for run in plan.runs
        }
    return {
        run.member: {
            "season_cache_ready": ready[run.member],
            "issue_dates_planned": len(run.issue_dates),
            # A set still generating covers some dates without being ready.
            "issue_dates_missing": [] if ready[run.member] else missing[run.member],
            "gpu_estimate": asdict(
                estimate_rollout(
                    0 if ready[run.member] else len(missing[run.member]),
                    ensemble=run.model.ensemble is not None,
                )
            ),
        }
        for run in plan.runs
    }


async def check_forecast(body: ForecastCreate, user_id: str) -> dict:
    """Per model: its live model or why it has none, whether its season cache is
    ready, the issue dates still missing and a GPU time estimate; plus where the
    season stands. Submits nothing."""
    basis = await job_submission.resolve_forecast_basis(body, user_id)
    registry = job_submission.get_packaged_forecast_models()
    today = job_submission.utc_today()
    season = resolve_season(basis.window, today, body.season_year)
    models = {name: _live_model(name, basis, registry) for name in basis.forecast_model_ids}
    result = {"season": season.model_dump(mode="json"), "models": list(models.values())}
    blockers = basis.blockers()
    reason = season.refusal or (
        f"{basis.subject} cannot run as a live forecast. " + " ".join(blockers)
        if blockers
        else None
    )
    if reason:
        return {**result, "can_run": False, "reason": reason}

    plan = job_submission.plan_live_season(
        basis.forecast_model_ids, basis.sources_by_name, registry, season, body.params, today=today
    )
    caches = await _cache_reports(plan)
    return {
        **result,
        "can_run": True,
        "scores_against_blend": body.blend_id is not None,
        "needs_gpu": job_submission.forecast_generation_gpus(
            {name: report["season_cache_ready"] for name, report in caches.items()}
        )
        > 0,
        "models": [{**models[name], **caches[name]} for name in basis.forecast_model_ids],
    }
