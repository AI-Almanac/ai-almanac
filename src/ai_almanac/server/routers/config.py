from fastapi import APIRouter

from ai_almanac.settings import get_metric_definitions, get_romp_defaults

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/metrics")
def list_metrics() -> list[dict]:
    return get_metric_definitions()


@router.get("/romp-defaults")
def romp_defaults() -> dict:
    return get_romp_defaults()
