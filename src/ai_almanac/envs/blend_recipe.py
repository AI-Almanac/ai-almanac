"""A blend job's onset_blending recipe (a BlendConfig, as a dict).

The job config carries what the user chose (years, formula, onset rule
overrides, area of interest) and what registration recorded about each data
source (variables, dimension names, units, the observations' date
convention). Everything else is what the Modal workflow
(modal/blending_app.py) builds blends with, so a blend trained locally
through onset_blending is the one the workflow trains: the same onset rule,
climatology, weekly features, formulas and scoring.

Pure: no onset_blending import, so the server's tests can check the recipe;
the blending env turns it into a BlendConfig (envs/blend_entrypoint.py).
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_THRESHOLD_MM = 20.0
DEFAULT_SEASON_START = "05-01"
DEFAULT_MIN_ONSET_YEARS = 10

# The onset definitions a blend can use, in BlendConfig's terms. A region
# uses its own (REGION_ONSET_DEFINITIONS), others ICPAC's; a job may name one
# (blend_params.onset_definition), and its threshold_mm and
# ref_onset_month_day override the definition's.
ONSET_DEFINITIONS = {
    # ICPAC's: 3 wet days (each at least 1 mm) totalling more than 20 mm, not
    # followed by a dry spell in the next 21 days. The workflow's ONSET_RULE.
    "icpac": {
        "definition": {
            "window": 3,
            "wet_day_min_mm": 1.0,
            "follow_days": 21,
            "dry_spell": {"mode": "consecutive_dry", "min_dry_days": 5, "dry_day_min_mm": 1.0},
        },
        "threshold": {"mm": DEFAULT_THRESHOLD_MM},
        "reference_onset": "06-01",
    },
    # Moron and Robertson's local onset, modified with the MOK filter: the
    # first wet day (at least 1 mm) of the first 5-day spell totalling at
    # least the cell's climatological 5-day amount, on or after June 2 (the
    # median onset over Kerala, 1901-2024), with no dry-spell check (it would
    # lean on long-lead rainfall forecasts). The local amount is 5 x the cell's
    # mean daily rain over April-October of 1991-2020 (ROMP's registry,
    # moron_robertson_mok).
    "moron_robertson_mok": {
        "definition": {
            "window": 5,
            "trigger_rule": "first_day_wet",
            "wet_day_min_mm": 1.0,
            "wet_day_comparison": "gte",
            "threshold_comparison": "gte",
            "follow_days": 0,
        },
        "threshold": {
            "climatology": {"days": 5, "start": "04-01", "end": "10-31", "years": [1991, 2020]}
        },
        "reference_onset": "06-02",
        "fixed_cutoff": "06-02",
    },
}
REGION_ONSET_DEFINITIONS = {"india": "moron_robertson_mok"}
DEFAULT_ONSET_DEFINITION = "icpac"
FORECAST_WINDOW = 45
GRID_ID_DIGITS = 2
OBS_VARIABLE = "RAINFALL"
FORECAST_VARIABLE = "tp"

# Regions whose blends are on administrative units (ADM3), regridded with the
# blending checkout's district weights (blending_app.ADM3_DOMAIN_REGIONS).
ADM3_REGIONS = {"ethiopia"}
ADM3_WEIGHTS = Path("Monsoon_Data") / "grid_to_district_mapping.csv"

# Registered dimension roles (data_sources._forecast_dims) as BlendConfig names.
_ROLE_NAMES = {"init_time": "time", "step": "day", "member": "number", "lat": "lat", "lon": "lon"}

# A job's day-level settings (server DayLevelSettings) as onset_blending's
# random forest settings; an unset one keeps the forest's default, which is
# onset_forest's (the workflow's day-level blend).
_FOREST_SETTINGS = ("years_per_fold", "trees", "max_depth", "min_samples_leaf", "max_samples")

# Stored formula texts name the climatology features as the workflow's
# connector did; onset_blending calls them prob_clim.
_OLD_CLIMATOLOGY_FEATURE = "prob_clim_mr"
CLIMATOLOGY_FEATURE = "prob_clim"


def default_formula(model_names: list[str]) -> str:
    terms = [f"{CLIMATOLOGY_FEATURE}_qx"] + [f"diff_{name}_qx" for name in model_names]
    return "outcome ~ " + " * ".join(terms)


def formula(text: str | None, model_names: list[str]) -> str:
    """The job's formula in onset_blending's feature names."""
    if not text:
        return default_formula(model_names)
    return text.replace(_OLD_CLIMATOLOGY_FEATURE, CLIMATOLOGY_FEATURE)


def model_dims(layout: dict | None) -> dict[str, str]:
    """A source's registered dimension names, renamed to the roles blending reads."""
    dims = (layout or {}).get("forecast_dims") or {}
    return {name: _ROLE_NAMES[role] for role, name in dims.items() if role in _ROLE_NAMES}


def blend_config(job: dict, *, work_dir: Path, blending_root: Path | None = None) -> dict:
    """The BlendConfig dict for a blend job's config. work_dir receives the
    area-of-interest outlines and unit centroids the config points to;
    blending_root holds the ADM3 district weights."""
    params = job.get("blend_params") or {}
    model_names = list(job.get("model_names") or [])
    if not model_names:
        raise ValueError("Blend config has no models")
    layouts = job.get("model_layouts") or {}
    region = (job.get("region_id") or "").strip().lower()

    domain: dict = {"grid_ids": {"decimal_digits": GRID_ID_DIGITS, "format": "fixed"}}
    weights = None
    if region in ADM3_REGIONS:
        if blending_root is None:
            raise ValueError(f"The {region} blend needs the blending checkout's ADM3 weights")
        weights = Path(blending_root) / ADM3_WEIGHTS
        domain["regrid"] = {"weights": str(weights)}
    area = _area_of_interest(params.get("focus_area"), Path(work_dir), weights, domain)
    if area:
        domain["area_of_interest"] = area

    models = []
    for name in model_names:
        files = [str(path) for path in (job.get("model_files") or {}).get(name) or []]
        if not files:
            raise ValueError(f"No forecast files configured for model {name!r}")
        layout = layouts.get(name) or {}
        models.append(
            {
                "name": name,
                "files": {"paths": files},
                "variable": FORECAST_VARIABLE,
                "dims": model_dims(layout),
                # Only for files that declare no units: each file's declared units decide.
                "unit_scale": float(layout.get("unit_cvt") or 1.0),
                "rain_predictors": [{"agg": "diff", "window": "trigger"}],
            }
        )

    years = {
        "training": _years(params.get("training_years")),
        "cv_holdout": _years(params.get("cv_holdout_years")),
    }
    if params.get("true_holdout_years"):
        years["true_holdout"] = _years(params["true_holdout_years"])
    evaluation = {
        "years": years,
        "formulas": {"blended_model": formula(params.get("formula_text"), model_names)},
    }
    if job.get("train_day_level_blend"):
        evaluation["random_forest"] = random_forest(params.get("day_level"))
    return {
        "name": job.get("blend_name") or "blend",
        "domain": domain,
        "observations": {
            "files": {"folder": str(job["obs_dir"]), "pattern": r"\.nc$"},
            "variable": params.get("obs_value_col") or OBS_VARIABLE,
            "date_marks": job.get("obs_date_marks") or "end",
        },
        "models": models,
        "onset": onset(params, region),
        "climatology": {
            "forecast_window": FORECAST_WINDOW,
            "cv_by_year": False,
            "min_onset_years": int(params.get("min_onset_years") or DEFAULT_MIN_ONSET_YEARS),
        },
        "evaluation": evaluation,
    }


def random_forest(settings: dict | None) -> dict:
    """The day-level blend: onset_blending's random forest, scored and saved
    as the workflow's (blended_forest), with the job's day-level settings."""
    settings = settings or {}
    return {key: settings[key] for key in _FOREST_SETTINGS if settings.get(key) is not None}


def onset_definition_name(params: dict, region: str) -> str:
    """The job's onset definition: the one it names, else its region's."""
    name = params.get("onset_definition") or REGION_ONSET_DEFINITIONS.get(
        region, DEFAULT_ONSET_DEFINITION
    )
    if name not in ONSET_DEFINITIONS:
        raise ValueError(
            f"Unknown onset definition {name!r}: one of {', '.join(ONSET_DEFINITIONS)}"
        )
    return name


def onset(params: dict, region: str) -> dict:
    """The BlendConfig onset section: the definition, with the job's own
    threshold, search start and reference onset where it sets them."""
    chosen = ONSET_DEFINITIONS[onset_definition_name(params, region)]
    threshold = chosen["threshold"]
    if params.get("threshold_mm"):
        threshold = {"mm": float(params["threshold_mm"])}
    reference = (
        params.get("ref_onset_month_day")
        or params.get("mok_month_day")
        or chosen["reference_onset"]
    )
    out = {
        "threshold": threshold,
        "season_start": params.get("cutoff_month_day") or DEFAULT_SEASON_START,
        "reference_onset": {"month_day": reference},
        "definition": chosen["definition"],
    }
    if "fixed_cutoff" in chosen:
        out["fixed_cutoff"] = chosen["fixed_cutoff"]
    return out


def _years(value) -> list[int]:
    """A job's year spec ("2008:2022", "2020,2021" or a list), read as the
    workflow's _parse_years reads it."""
    if isinstance(value, list | tuple):
        years = [int(year) for year in value]
    else:
        years = []
        for token in filter(None, (part.strip() for part in str(value or "").split(","))):
            if ":" in token:
                first, last = token.split(":", 1)
                years.extend(range(int(first), int(last) + 1))
            else:
                years.append(int(token))
    if not years:
        raise ValueError("Blend params need training and CV holdout years")
    return sorted(dict.fromkeys(years))


def _area_of_interest(
    focus: dict | None, work_dir: Path, weights: Path | None, domain: dict
) -> dict | None:
    """The job's focus area as a BlendConfig area of interest: a box, or the
    picked units' outlines as a GeoJSON file. Units without coordinates
    (ADM3 districts) are located by their centroids."""
    if not focus:
        return None
    work_dir.mkdir(parents=True, exist_ok=True)
    if weights is not None:
        domain["centroids"] = {
            "file": str(_adm3_centroids(weights, work_dir / "unit_centroids.csv")),
            "id_col": "id",
        }
    if focus.get("geometry"):
        path = work_dir / "area_of_interest.geojson"
        path.write_text(json.dumps(focus["geometry"]))
        return {"geometry": str(path)}
    return {
        "bbox": {key: float(focus[key]) for key in ("lat_min", "lat_max", "lon_min", "lon_max")}
    }


def _adm3_centroids(weights: Path, out: Path) -> Path:
    """Each district's weight-averaged grid point, as the workflow locates them."""
    import pandas as pd

    mapping = pd.read_csv(weights).rename(columns={"latitude": "lat", "longitude": "lon"})
    mapping["weight"] = mapping["weight"].astype(float)
    mapping["weighted_lat"] = mapping["lat"].astype(float) * mapping["weight"]
    mapping["weighted_lon"] = mapping["lon"].astype(float) * mapping["weight"]
    grouped = mapping.groupby("adm3_name", as_index=False)[
        ["weight", "weighted_lat", "weighted_lon"]
    ].sum()
    grouped["lat"] = grouped["weighted_lat"] / grouped["weight"]
    grouped["lon"] = grouped["weighted_lon"] / grouped["weight"]
    grouped.rename(columns={"adm3_name": "id"})[["id", "lat", "lon"]].to_csv(out, index=False)
    return out
