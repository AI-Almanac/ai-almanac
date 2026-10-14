"""Live AI weather forecast generation — season planning, earth2studio
rollouts, the trajectory store, and season assembly.

Deliberately self-contained: only stdlib and scientific-stack imports
(earth2studio, xarray, numpy), never anything else from
`ai_almanac` — this is what lets the exact same file run in two places:

  - Bundled as a single file into the Modal image (see modal/forecasts_app.py,
    which is a thin wrapper handling only Modal-specific plumbing: GPU/image
    selection, the CPU/GPU split, the weights Volume, cross-app calls).
  - Imported normally inside the local `forecast` pixi environment (see
    envs/forecast_entrypoint.py), which has no Modal dependency at all.

Callers resolve model registry entries (server/config/forecast_models.yaml)
and any local/GCS staging themselves and pass plain dicts/Paths in — this
module has no opinion on where the model registry or job config come from.
"""

from __future__ import annotations

import datetime as dt
import gc
import io
import os
import tarfile
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Bump when the reduction logic, units, or on-disk schema of a cached
# trajectory changes so stale entries become misses instead of being served
# with the wrong shape (D4). It is part of the cache key.
TRAJECTORY_CACHE_VERSION = 1

# Single canonical rollout horizon for the trajectory store (D4). The store
# always holds the full trajectory, so lead is a fixed key segment rather than
# a variable that splits the same rollout across `lead30d`/`lead45d` keys.
CANONICAL_LEAD_DAY = 45


# ---------------------------------------------------------------------------
# earth2studio inference
# ---------------------------------------------------------------------------


# Selectable earth2studio analysis sources a live rollout can initialize from.
# Single source of truth for both resolve_data_source (which object to build)
# and the API's init-source list (what the UI offers as a dropdown). Both are
# zero-config, no-auth analysis sources whose ds(time, variable) output feeds
# earth2studio's deterministic(), and both carry the full model input vocab
# (soil, precip, ...) over a season-to-date:
#   - GFS: NOAA operational analysis (2021->present). The default — proven in
#     production for the registered models.
#   - IFS: ECMWF HRES operational analysis (2024-03->present).
#
# ERA5 is intentionally absent: NCAR_ERA5 (free, live) has no precip in its
# lexicon so it can't init precip models, and ARCO (free, complete) is frozen
# at 2025-12-31. The only free+live+complete ERA5 is CDS, which needs an API
# key — wire that (and its auth) before offering ERA5 here.
INIT_SOURCES: dict[str, dict[str, str]] = {
    "gfs": {"display_name": "GFS (NOAA)", "earth2studio_class": "GFS"},
    "ifs": {"display_name": "IFS (ECMWF)", "earth2studio_class": "IFS"},
}


def resolve_data_source(init_source: str):
    """Map an init-source name to its earth2studio data source object.

    The init source is part of a trajectory's identity (D6): the same model and
    date initialized from GFS versus ERA5 are different assets, so serving one
    for the other silently corrupts results. Register new earth2studio sources
    here by adding to INIT_SOURCES.
    """
    name = init_source.lower()
    entry = INIT_SOURCES.get(name)
    if entry is None:
        raise ValueError(
            f"Unknown forecast init source {init_source!r}; known: {sorted(INIT_SOURCES)}"
        )
    import earth2studio.data as e2s_data

    # No disk cache: every init date is fetched once (the trajectory store is
    # the cache), and on Modal the default cache root is the weights Volume, so
    # cached init data would be uploaded on every commit.
    return getattr(e2s_data, entry["earth2studio_class"])(cache=False)


def load_model(model_class: str):
    import torch
    from earth2studio.models import px

    torch.set_default_device("cuda")
    cls = getattr(px, model_class)
    package = cls.load_default_package()
    return cls.load_model(package)


class PlanError(ValueError):
    """A forecast plan, or an input it is built from, is missing or malformed."""


@dataclass(frozen=True)
class EnsembleSpec:
    """How an ensemble model is rolled out.

    Ensemble models (AIFS-ENS) carry their own initial-condition noise, so the
    spread comes from the model, not from perturbing the input — see run_rollout.
    The members are averaged to the ensemble mean downstream (the mean keeps the
    day×lat×lon schema the blend was trained on).
    """

    members: int
    batch_size: int


def ensemble_config(model_entry: dict) -> EnsembleSpec | None:
    """Ensemble settings from a registry entry, or None for deterministic models."""
    if not model_entry.get("ensemble"):
        return None
    members = int(model_entry.get("nensemble") or 8)
    # Members run in batches to bound VRAM — a global-resolution member is large,
    # so default to one at a time and let a model override it.
    batch_size = int(model_entry.get("nensemble_batch") or 1)
    if members < 1 or batch_size < 1:
        raise ValueError(
            f"nensemble and nensemble_batch must be at least 1, got {members}, {batch_size}"
        )
    return EnsembleSpec(members=members, batch_size=min(batch_size, members))


def run_rollout(
    model, data, init_times: list[str], nsteps: int, io_backend, ensemble_spec: EnsembleSpec | None
):
    """Roll one model forward, writing to io_backend. Deterministic models use
    the single-trajectory workflow; ensemble models use earth2studio's ensemble
    workflow with a Zero perturbation (spread is the model's internal noise)."""
    if ensemble_spec is None:
        from earth2studio.run import deterministic

        deterministic(init_times, nsteps, model, data, io_backend)
        return
    from earth2studio.perturbation import Zero
    from earth2studio.run import ensemble

    ensemble(
        init_times,
        nsteps,
        ensemble_spec.members,
        model,
        data,
        io_backend,
        Zero(),
        batch_size=ensemble_spec.batch_size,
    )


def lead_steps(lead_hours: list[int], model_class: str) -> tuple[int, int]:
    step_hours = 12 if model_class == "GenCastMini" else 6
    return max(lead_hours) // step_hours, step_hours


# ---------------------------------------------------------------------------
# Season-long inference for live blend scoring.
#
# The blend's onset-detection pipeline expects one forecast *issued* every
# few days across a season (time dim) each with a multi-week lead trajectory
# (day dim) — see testdata/ethiopia/fuxi/2000.nc: time=26 issue dates x
# day=0..45. A single earth2studio run only gives one issue date with a
# short lead window, so this loops the model across the season-to-date on
# the same weekday cadence its historical archive was built on.
# ---------------------------------------------------------------------------


def season_issue_dates(
    season_start_month_day: str,
    init_weekdays: list[int],
    year: int,
    schedule_month_days: list[str] | None = None,
    *,
    today: dt.date,
) -> list[dt.date]:
    """Issue dates in [season_start, today] for one season/model.

    The archives pin issue dates to fixed *calendar dates* (e.g. Apr 1, 4, 8),
    not weekdays — the weekday of a given issue date drifts year to year, so a
    weekday grid inferred from one year silently disagrees with another. When a
    source's month-day schedule (a list of ``MM-DD``) is known, reproduce those
    exact calendar dates: this lands a live rollout on the same seasonal
    positions the blend was trained on, and lets two regions whose schedules
    share calendar dates share cached trajectories.

    ``init_weekdays`` (Python numbering, Monday=0..Sunday=6) is the fallback for
    sources registered before a schedule was inferred.
    """
    month, day = (int(part) for part in season_start_month_day.split("-"))
    start = dt.date(year, month, day)
    end = min(today, dt.date(year, 12, 31))

    if schedule_month_days:
        dates = []
        for entry in schedule_month_days:
            entry_month, entry_day = (int(part) for part in entry.split("-"))
            try:
                candidate = dt.date(year, entry_month, entry_day)
            except ValueError:
                continue  # e.g. 02-29 in a non-leap year — no such date this year
            if start <= candidate <= end:
                dates.append(candidate)
        return sorted(dates)

    dates = []
    current = start
    while current <= end:
        if current.weekday() in init_weekdays:
            dates.append(current)
        current += dt.timedelta(days=1)
    return dates


# ---------------------------------------------------------------------------
# Season plan.
#
# Everything a season rollout needs is resolved once, at submission, into a
# frozen SeasonPlan stored in the job config: which registry model each blend
# member runs, how its archive was laid out, and the exact issue dates. The
# readiness gate, both runners, and both coverage markers read that one plan,
# so they cannot disagree, and nothing after submission consults the clock.
# ---------------------------------------------------------------------------


def _parse_weekdays(raw) -> tuple[int, ...]:
    parts = raw if isinstance(raw, list | tuple) else str(raw).split(",")
    try:
        weekdays = tuple(int(part) for part in parts)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"init_days must be weekdays 0-6, got {raw!r}") from exc
    if not weekdays or any(not 0 <= day <= 6 for day in weekdays):
        raise PlanError(f"init_days must be weekdays 0-6, got {raw!r}")
    return weekdays


def _parse_month_day(raw, what: str = "init_month_days entries") -> str:
    try:
        # 2000 is a leap year, so 02-29 is a valid schedule entry.
        return dt.datetime.strptime(f"2000-{raw}", "%Y-%m-%d").strftime("%m-%d")
    except (TypeError, ValueError) as exc:
        raise PlanError(f"{what} must be MM-DD, got {raw!r}") from exc


def _parse_season_start(raw, season: int) -> str:
    month_day = _parse_month_day(raw, "The season start")
    month, day = (int(part) for part in month_day.split("-"))
    try:
        dt.date(season, month, day)
    except ValueError as exc:
        raise PlanError(f"The season start {raw!r} is not a date in {season}") from exc
    return month_day


@dataclass(frozen=True)
class Bounds:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    @classmethod
    def parse(cls, raw: dict | None) -> Bounds | None:
        """None when absent or incomplete, which keeps the season global."""
        values = [(raw or {}).get(name) for name in ("lat_min", "lat_max", "lon_min", "lon_max")]
        if any(value is None for value in values):
            return None
        try:
            return cls(*(float(value) for value in values))
        except (TypeError, ValueError) as exc:
            raise PlanError(f"spatial_bounds must be numbers, got {raw!r}") from exc

    def to_dict(self) -> dict:
        return {
            "lat_min": self.lat_min,
            "lat_max": self.lat_max,
            "lon_min": self.lon_min,
            "lon_max": self.lon_max,
        }


@dataclass(frozen=True)
class ForecastModel:
    """The registry model a blend member runs (server/config/forecast_models.yaml)."""

    registry_id: str
    earth2studio_class: str
    env: str
    ensemble: EnsembleSpec | None

    @classmethod
    def parse(cls, entry: dict) -> ForecastModel:
        """From a registry entry, or from to_dict's output (the same shape)."""
        try:
            return cls(
                registry_id=str(entry["id"]),
                earth2studio_class=str(entry["earth2studio_class"]),
                env=str(entry.get("env") or "base"),
                ensemble=ensemble_config(entry),
            )
        except KeyError as exc:
            raise PlanError(f"forecast model entry is missing {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise PlanError(
                f"forecast model {entry.get('id')!r} has unusable settings: {exc}"
            ) from exc

    def to_dict(self) -> dict:
        out = {
            "id": self.registry_id,
            "earth2studio_class": self.earth2studio_class,
            "env": self.env,
        }
        if self.ensemble is not None:
            out |= {
                "ensemble": True,
                "nensemble": self.ensemble.members,
                "nensemble_batch": self.ensemble.batch_size,
            }
        return out


@dataclass(frozen=True)
class ArchiveLayout:
    """How a member's historical archive was built, from its data source's
    metadata. The live season must match it to score against the blend."""

    init_weekdays: tuple[int, ...]
    init_month_days: tuple[str, ...]
    unit_cvt: float
    spatial_bounds: Bounds | None

    @classmethod
    def parse(cls, metadata: dict) -> ArchiveLayout:
        unit_cvt = metadata.get("unit_cvt")
        try:
            unit_cvt = 1.0 if unit_cvt is None else float(unit_cvt)
        except (TypeError, ValueError) as exc:
            raise PlanError(f"unit_cvt must be a number, got {unit_cvt!r}") from exc
        return cls(
            init_weekdays=_parse_weekdays(metadata.get("init_days") or "0,3"),
            init_month_days=tuple(
                _parse_month_day(entry) for entry in metadata.get("init_month_days") or ()
            ),
            unit_cvt=unit_cvt,
            spatial_bounds=Bounds.parse(metadata.get("spatial_bounds")),
        )


@dataclass(frozen=True)
class ModelRun:
    """One blend member's season rollout."""

    member: str
    model: ForecastModel
    unit_cvt: float
    spatial_bounds: Bounds | None
    issue_dates: tuple[dt.date, ...]

    def __post_init__(self) -> None:
        if not self.issue_dates:
            raise PlanError(f"No issue dates for {self.member} this season yet")

    @classmethod
    def parse(cls, raw: dict) -> ModelRun:
        return cls(
            member=str(raw["member"]),
            model=ForecastModel.parse(raw["model"]),
            unit_cvt=float(raw["unit_cvt"]),
            spatial_bounds=Bounds.parse(raw.get("spatial_bounds")),
            issue_dates=tuple(dt.date.fromisoformat(d) for d in raw["issue_dates"]),
        )

    def to_dict(self) -> dict:
        return {
            "member": self.member,
            "model": self.model.to_dict(),
            "unit_cvt": self.unit_cvt,
            "spatial_bounds": self.spatial_bounds.to_dict() if self.spatial_bounds else None,
            "issue_dates": [d.isoformat() for d in self.issue_dates],
        }


@dataclass(frozen=True)
class SeasonPlan:
    """Every member's season rollout for one forecast job, frozen at submission."""

    season: int
    init_source: str
    lead_day: int
    runs: tuple[ModelRun, ...]

    def __post_init__(self) -> None:
        if self.init_source not in INIT_SOURCES:
            raise PlanError(f"Unknown init source {self.init_source!r}")
        if not self.runs:
            raise PlanError("A season plan needs at least one model run")
        members = [run.member for run in self.runs]
        if len(set(members)) != len(members):
            raise PlanError(f"A season plan lists a member twice: {members}")
        if self.lead_day < 1:
            raise PlanError(f"lead_day must be at least 1, got {self.lead_day}")

    @classmethod
    def parse(cls, raw) -> SeasonPlan:
        if not isinstance(raw, dict):
            raise PlanError("The job config has no season plan")
        try:
            return cls(
                season=int(raw["season"]),
                init_source=str(raw["init_source"]),
                lead_day=int(raw["lead_day"]),
                runs=tuple(ModelRun.parse(run) for run in raw["runs"]),
            )
        except PlanError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError(f"Malformed season plan: {exc!r}") from exc

    def to_dict(self) -> dict:
        return {
            "season": self.season,
            "init_source": self.init_source,
            "lead_day": self.lead_day,
            "runs": [run.to_dict() for run in self.runs],
        }

    def run(self, member: str) -> ModelRun:
        for run in self.runs:
            if run.member == member:
                return run
        raise PlanError(f"The season plan has no run for {member!r}")

    def key(self, run: ModelRun) -> TrajectoryKey:
        return TrajectoryKey(run.model.registry_id, self.init_source, self.lead_day)

    def covered_dates(self) -> dict[str, list[str]]:
        """Issue dates each member's rollout covers: what the readiness gate
        checks and what the coverage markers record."""
        return {run.member: [d.isoformat() for d in run.issue_dates] for run in self.runs}


def plan_season(
    members: Mapping[str, tuple[ForecastModel, ArchiveLayout]],
    *,
    season: int,
    season_start_month_day: str,
    init_source: str,
    today: dt.date,
    max_issue_dates: int | None = None,
    lead_day: int = CANONICAL_LEAD_DAY,
) -> SeasonPlan:
    """Resolve each blend member's season rollout. Pure: the clock is `today`.

    Every member rolls out on the union of the archives' calendar schedules.
    The blend inner-joins members on issue date, so two archives built on
    different calendars (e.g. FuXi's twice-weekly grid vs AIFS-single-v2's
    1st/7th/9th/... grid) would otherwise leave only their few shared dates; the
    climatology grid is daily, so any date within the season scores. When no
    archive has a schedule, each member keeps its weekday fallback.

    `max_issue_dates` keeps only the most recent N dates — a smoke-test knob, as
    is a `lead_day` shorter than the canonical horizon.
    """
    season_start = _parse_season_start(season_start_month_day, season)
    shared_month_days = sorted(
        {entry for _, layout in members.values() for entry in layout.init_month_days}
    )
    runs = []
    for member, (model, layout) in members.items():
        dates = season_issue_dates(
            season_start,
            list(layout.init_weekdays),
            season,
            shared_month_days or None,
            today=today,
        )
        if max_issue_dates:
            dates = dates[-max_issue_dates:]
        runs.append(ModelRun(member, model, layout.unit_cvt, layout.spatial_bounds, tuple(dates)))
    return SeasonPlan(season, init_source, lead_day, tuple(runs))


def _daily_precip_trajectory(
    model,
    data,
    issue_date: dt.date,
    max_lead_day: int,
    step_hours: int,
    scratch_root: Path,
    ensemble_spec: EnsembleSpec | None,
):
    """Run one issue date out to max_lead_day and reduce to one daily precip
    total per lead day (ROMP's `day` dimension), at the model's native grid.
    Ensemble models are reduced to the ensemble mean (see run_rollout).
    """
    import shutil

    import numpy as np
    import xarray as xr
    from earth2studio.io import ZarrBackend

    nsteps = (max_lead_day * 24) // step_hours
    zarr_path = scratch_root / f"{issue_date.isoformat()}.zarr"
    io_backend = ZarrBackend(str(zarr_path))
    t0 = time.perf_counter()
    run_rollout(
        model,
        data,
        [issue_date.strftime("%Y-%m-%dT%H:%M:%S")],
        nsteps,
        io_backend,
        ensemble_spec,
    )
    print(f"    rollout done in {time.perf_counter() - t0:.1f}s", flush=True)
    t0 = time.perf_counter()

    # Open lazily — do NOT call .load() on the full dataset. At global
    # resolution a model like AIFS/FuXi writes 10-20 GB across all pressure
    # levels and variables; loading everything before selecting tp causes OOM
    # after just a few rollouts. Instead, select tp lazily and .compute() one
    # day's sum at a time so peak memory per rollout is ~tp-only.
    dataset = xr.open_zarr(zarr_path)
    tp = select_variable(dataset, "tp")
    # Ensemble models write a member dim; average to the ensemble mean before
    # reducing (kept lazy, so peak memory stays ~one member). Mean and per-day
    # sum commute, so meaning first gives the same daily totals.
    if "ensemble" in tp.dims:
        tp = tp.mean("ensemble")
    tp = tp.squeeze()
    lat_name, lon_name = lat_lon_names(tp)
    # unit_cvt (from the archived data source's metadata, applied by the
    # caller) converts the model's native units to the mm/day convention
    # ROMP/blending expect.
    # ponytail: native model grid, not reprojected to match the historical
    # archive's exact lat/lon cells — the blend joins by rounded lat/lon id,
    # so close-enough grids still join, just not cell-for-cell identical.
    # Upgrade: regrid to the archived source's exact grid if join quality
    # against historical years turns out to matter.
    lead_hours_coord = (tp["lead_time"].values / np.timedelta64(1, "h")).astype(int)
    daily_totals = []
    for lead_day in range(max_lead_day + 1):
        day_start = lead_day * 24
        day_end = day_start + 24
        mask = (lead_hours_coord >= day_start) & (lead_hours_coord < day_end)
        if not mask.any():
            daily_totals.append(xr.full_like(tp.isel(lead_time=0), np.nan).compute())
            continue
        window = tp.isel(lead_time=np.where(mask)[0])
        # tp06/tp12 are period accumulations (precip since the *previous*
        # step, resetting every step), not cumulative-since-init — so a
        # day's total is the sum of the steps inside it, not a diff across
        # days.
        daily_totals.append(window.sum(dim="lead_time").compute())

    # Release all zarr file handles before rmtree so nothing is left open.
    dataset.close()
    del tp
    print(f"    zarr reduce done in {time.perf_counter() - t0:.1f}s", flush=True)
    shutil.rmtree(zarr_path, ignore_errors=True)

    stacked = xr.concat(daily_totals, dim="day")
    stacked = stacked.assign_coords(day=list(range(max_lead_day + 1)))
    per_day = stacked.clip(min=0)
    result = per_day.transpose("day", lat_name, lon_name)
    return result


def select_lat_lon_bounds(data_array, bounds: Bounds):
    lat_name, lon_name = lat_lon_names(data_array)
    lat_bounds = (bounds.lat_min, bounds.lat_max)
    lon_bounds = (bounds.lon_min, bounds.lon_max)
    lat_values = data_array[lat_name].values
    reverse = bool(len(lat_values) >= 2 and lat_values[0] > lat_values[-1])
    lat_slice = slice(*sorted(lat_bounds, reverse=reverse))
    return data_array.sel({lat_name: lat_slice, lon_name: slice(min(lon_bounds), max(lon_bounds))})


_gcs_client_singleton: Any = None


def _split_gs_uri(uri: str) -> tuple[str, str]:
    """Split gs://bucket/some/key into ("bucket", "some/key")."""
    without = uri[len("gs://") :] if uri.startswith("gs://") else uri
    bucket, _, key = without.partition("/")
    return bucket, key


def _gcs_client():
    """One shared google-cloud-storage client.

    Uses the synchronous google-cloud-storage client on purpose rather than
    gcsfs: gcsfs runs a background asyncio loop with aiohttp sessions and DNS
    resolver threads that are never torn down, so a mostly-cached season (many
    quick reads, little GPU time) returns with those threads still alive and
    Modal cannot shut the container down — it retries the whole loop instead.
    """
    global _gcs_client_singleton
    from google.cloud import storage

    if _gcs_client_singleton is None:
        _gcs_client_singleton = storage.Client()
    return _gcs_client_singleton


def _gcs_blob(gs_uri: str):
    bucket_name, key = _split_gs_uri(gs_uri)
    return _gcs_client().bucket(bucket_name).blob(key)


# ---------------------------------------------------------------------------
# Trajectory store.
#
# A rollout initialized from a past date is deterministic (fixed weights,
# archived init conditions), so its reduced trajectory never changes — store it
# and every later forecast job skips that GPU run. Stored at the model's native
# grid and units (unit_cvt/bounds are applied at assembly), so one entry serves
# any blend using the model.
#
# The store root may be a local directory or a gs:// URI.
# ---------------------------------------------------------------------------

_TRAJECTORY_ENCODING = {"tp": {"zlib": True, "complevel": 1}}


@dataclass(frozen=True)
class TrajectoryKey:
    """Which stored trajectories a rollout produces and a season reads.

    Hardened per D4: the init source (a GFS rollout is a different asset than
    the same date from IFS), a reduction/schema version so bumps invalidate
    stale entries, and the rollout horizon. Real forecasts always use the
    canonical 45-day horizon; a shorter one exists only for smoke tests and
    lands under its own `lead{N}d` prefix, so it can never be served as a
    full-length trajectory.
    """

    model_id: str
    init_source: str
    lead_day: int = CANONICAL_LEAD_DAY

    def __post_init__(self) -> None:
        if self.lead_day < 1:
            raise ValueError(f"lead_day must be at least 1, got {self.lead_day}")

    def prefix(self, store: Path | str) -> str:
        return (
            f"{str(store).rstrip('/')}/{self.model_id}/{self.init_source}"
            f"/v{TRAJECTORY_CACHE_VERSION}/lead{self.lead_day}d"
        )

    def uri(self, store: Path | str, issue_date: dt.date) -> str:
        return f"{self.prefix(store)}/{issue_date.isoformat()}.nc"


def _issue_date_from_name(name: str) -> dt.date | None:
    stem = name.rsplit("/", 1)[-1].removesuffix(".nc")
    try:
        return dt.date.fromisoformat(stem)
    except ValueError:
        return None


def stored_issue_dates(store: Path | str, key: TrajectoryKey) -> set[dt.date]:
    """Issue dates the store already holds for a key, from a single listing of
    its prefix rather than one lookup per date."""
    prefix = key.prefix(store)
    if prefix.startswith("gs://"):
        bucket, blob_prefix = _split_gs_uri(prefix)
        names = [b.name for b in _gcs_client().list_blobs(bucket, prefix=f"{blob_prefix}/")]
    else:
        names = [path.name for path in Path(prefix).glob("*.nc")]
    return {date for date in map(_issue_date_from_name, names) if date is not None}


def unstored_issue_dates(
    store: Path | str, key: TrajectoryKey, issue_dates: list[dt.date]
) -> list[dt.date]:
    """The issue dates that still need a GPU rollout, in their original order."""
    stored = stored_issue_dates(store, key)
    return [date for date in issue_dates if date not in stored]


def read_stored_trajectory(store: Path | str, key: TrajectoryKey, issue_date: dt.date):
    import xarray as xr

    uri = key.uri(store, issue_date)
    if not uri.startswith("gs://"):
        with xr.open_dataarray(uri) as da:
            return da.load()
    with tempfile.TemporaryDirectory(prefix="trajectory-") as tmp:
        local_path = Path(tmp) / "trajectory.nc"
        _gcs_blob(uri).download_to_filename(str(local_path))
        with xr.open_dataarray(local_path, engine="h5netcdf") as da:
            return da.load()


def write_stored_trajectory(
    store: Path | str, key: TrajectoryKey, issue_date: dt.date, trajectory
) -> None:
    import uuid

    uri = key.uri(store, issue_date)
    if uri.startswith("gs://"):
        # ponytail: no eviction — a season accumulates at most ~50 entries per
        # model; add cleanup if the bucket grows unbounded at season boundaries.
        with tempfile.TemporaryDirectory(prefix="trajectory-") as tmp:
            local_path = Path(tmp) / "trajectory.nc"
            trajectory.rename("tp").to_netcdf(local_path, encoding=_TRAJECTORY_ENCODING)
            _gcs_blob(uri).upload_from_filename(str(local_path))
        return
    # Atomic rename so a concurrent reader never sees a partial file.
    target = Path(uri)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    trajectory.rename("tp").to_netcdf(tmp_path, encoding=_TRAJECTORY_ENCODING)
    os.replace(tmp_path, target)


# ---------------------------------------------------------------------------
# Season rollouts (GPU) and assembly (CPU).
#
# Split so the GPU only ever runs rollouts: the caller sweeps the store once
# (unstored_issue_dates), rolls out just the missing dates, then assembles the
# season from the store. Modal runs the sweep and assembly in a CPU container
# and starts a GPU container only when something is missing.
# ---------------------------------------------------------------------------


def _request_key(time, variable) -> tuple:
    import numpy as np

    times = np.atleast_1d(np.asarray(time, dtype="datetime64[ns]"))
    return tuple(times.astype("int64").tolist()), tuple(str(v) for v in np.atleast_1d(variable))


class PrefetchingSource:
    """earth2studio data source that fetches known requests ahead of time on a
    background thread, so the next issue date's init conditions download while
    the current one rolls out on the GPU.

    Every fetch — prefetched or not — runs on the one worker thread: an
    earth2studio source is not safe to call concurrently (an uncached GFS
    deletes its temp download directory after each call).
    """

    def __init__(self, source):
        from concurrent.futures import ThreadPoolExecutor

        self._source = source
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="init-prefetch")
        self._pending: dict[tuple, Any] = {}

    def prefetch(self, requests: list[tuple]) -> None:
        for when, variable in requests:
            key = _request_key(when, variable)
            if key not in self._pending:
                self._pending[key] = self._executor.submit(self._source, when, variable)

    def discard(self, requests: list[tuple]) -> int:
        """Drop prefetched requests that were never consumed; returns how many.
        A non-zero count means the rollout asked for something else, so the
        prefetch is not saving anything for this model."""
        dropped = [self._pending.pop(_request_key(t, v), None) for t, v in requests]
        for future in dropped:
            if future is not None:
                future.cancel()
        return sum(future is not None for future in dropped)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._pending.clear()

    def __call__(self, time, variable):
        future = self._pending.pop(_request_key(time, variable), None)
        if future is None:
            future = self._executor.submit(self._source, time, variable)
        return future.result()


def init_requests(model, issue_date: dt.date) -> list[tuple]:
    """The (time, variable) calls earth2studio's workflows make to a data source
    to initialize `model` at `issue_date` — one per input lead time (see
    earth2studio.data.fetch_data)."""
    import numpy as np

    coords = model.input_coords()
    init_time = np.datetime64(issue_date, "ns")
    return [
        (np.array([init_time + lead], dtype="datetime64[ns]"), coords["variable"])
        for lead in coords["lead_time"]
    ]


def roll_out_issue_dates(
    run: ModelRun,
    key: TrajectoryKey,
    issue_dates: list[dt.date],
    scratch_root: Path,
    store: Path | str,
) -> None:
    """Roll one member's model out to the key's horizon for each issue date and
    write each trajectory to the store as soon as it finishes, so a failed run
    keeps the dates it paid for."""
    if not issue_dates:
        return
    model_class = run.model.earth2studio_class
    _, step_hours = lead_steps([key.lead_day * 24], model_class)
    scratch_root.mkdir(parents=True, exist_ok=True)
    print(
        f"==> Rolling out {len(issue_dates)} issue date(s) "
        f"({issue_dates[0]}..{issue_dates[-1]}), {(key.lead_day * 24) // step_hours} "
        f"steps ({step_hours}h each) per issue date",
        flush=True,
    )
    model = load_model(model_class)
    data = PrefetchingSource(resolve_data_source(key.init_source))
    try:
        for i, issue_date in enumerate(issue_dates):
            for upcoming in issue_dates[i : i + 2]:
                data.prefetch(init_requests(model, upcoming))
            t0 = time.perf_counter()
            print(f"  [{i + 1}/{len(issue_dates)}] issue date {issue_date}", flush=True)
            trajectory = _daily_precip_trajectory(
                model, data, issue_date, key.lead_day, step_hours, scratch_root, run.model.ensemble
            )
            write_stored_trajectory(store, key, issue_date, trajectory)
            if data.discard(init_requests(model, issue_date)):
                print("    warning: prefetched init data went unused", flush=True)
            print(f"  [{i + 1}/{len(issue_dates)}] done in {time.perf_counter() - t0:.1f}s")
            del trajectory
            gc.collect()
    finally:
        data.close()


def assemble_season_netcdf(
    store: Path | str,
    key: TrajectoryKey,
    run: ModelRun,
    scratch_root: Path,
    out_path: Path,
) -> Path:
    """Write out_path as a NetCDF matching the historical `{year}.nc` schema
    (time x day x lat x lon, variable tp) from the run's stored trajectories,
    applying its archive's unit conversion and spatial bounds."""
    import dask
    import numpy as np
    import xarray as xr

    scratch_root.mkdir(parents=True, exist_ok=True)

    slice_paths: list[Path] = []
    for i, issue_date in enumerate(run.issue_dates, start=1):
        trajectory = read_stored_trajectory(store, key, issue_date)
        # Write each slice to scratch immediately and release the in-memory
        # array — accumulating all N trajectories before xr.concat causes OOM
        # on long seasons. open_mfdataset reassembles lazily via dask below.
        traj = trajectory.assign_coords(time=np.datetime64(issue_date)) * run.unit_cvt
        if run.spatial_bounds:
            traj = select_lat_lon_bounds(traj, run.spatial_bounds)
        slice_path = scratch_root / f"slice_{i:03d}.nc"
        traj.rename("tp").to_netcdf(slice_path)
        slice_paths.append(slice_path)
        del trajectory, traj
        gc.collect()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # HDF5/h5py is not thread-safe; open_mfdataset + to_netcdf under dask's
    # default threaded scheduler deadlocks reading the slices while writing the
    # output (hangs indefinitely, no error). Force the single-threaded scheduler
    # — the write still streams slice-by-slice, so memory stays flat.
    with (
        dask.config.set(scheduler="single-threaded"),
        xr.open_mfdataset(slice_paths, concat_dim="time", combine="nested") as ds,
    ):
        ds.to_netcdf(out_path)
    return out_path


def generate_season_forecast_netcdf(
    plan: SeasonPlan,
    member: str,
    scratch_root: Path,
    out_path: Path,
    store: Path | str,
) -> Path:
    """In-process season pipeline (local runner): sweep the store, roll out only
    the member's missing issue dates, then assemble its season NetCDF. Modal runs
    the same three steps across CPU and GPU containers (modal/forecasts_app.py)."""
    run = plan.run(member)
    key = plan.key(run)
    missing = unstored_issue_dates(store, key, list(run.issue_dates))
    stored_count = len(run.issue_dates) - len(missing)
    print(f"==> {stored_count}/{len(run.issue_dates)} issue date(s) stored")
    roll_out_issue_dates(run, key, missing, scratch_root / "rollouts", store)
    return assemble_season_netcdf(store, key, run, scratch_root, out_path)


def bundle_files(files: list[Path]) -> bytes:
    """Tar.gz a set of files, matching modal/blending_app.py's forecast_bundles
    convention (one bundle per model, containing its `{year}.nc` files)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in files:
            tar.add(path, arcname=path.name)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Variable selection in earth2studio output
# ---------------------------------------------------------------------------


def variable_candidates(variable: str) -> tuple[str, ...]:
    known = {
        "t2m": ("t2m", "2t", "t2m_0m", "temperature_2m"),
        "u10": ("u10", "10u", "u10m", "u-component_of_wind_10m"),
        "v10": ("v10", "10v", "v10m", "v-component_of_wind_10m"),
        "tcwv": ("tcwv", "pwat"),
        "msl": ("msl", "prmsl"),
        "u850": ("u850",),
        "v850": ("v850",),
        "q850": ("q850",),
        "z500": ("z500",),
        # AIFS/FuXi/GraphCastSmall name their precip output "tp06" (6h
        # accumulation); GenCastMini's 12h-step output is "tp12". None of the
        # registered models expose a plain "tp".
        "tp": ("tp06", "tp12", "tp"),
    }
    return known.get(variable, (variable,))


def array_with_variable_coord(dataset):
    for name in ("fields", "output", "forecast"):
        if name in dataset and "variable" in dataset[name].coords:
            return dataset[name]
    for data_array in dataset.data_vars.values():
        if "variable" in data_array.coords:
            return data_array
    return None


def select_variable(dataset, variable: str):
    candidates = variable_candidates(variable)
    data_array = array_with_variable_coord(dataset)
    if data_array is not None:
        available = [str(v) for v in data_array.coords["variable"].values]
        for candidate in candidates:
            if candidate in available:
                return data_array.sel(variable=candidate)
        raise KeyError(f"None of {candidates} found in variables {available[:12]}")

    for candidate in candidates:
        if candidate in dataset.data_vars:
            return dataset[candidate]
    available = list(dataset.data_vars)
    raise KeyError(f"None of {candidates} found in forecast arrays {available[:12]}")


def lat_lon_names(data_array) -> tuple[str, str]:
    lat_name = next((n for n in ("lat", "latitude", "y") if n in data_array.coords), None)
    lon_name = next((n for n in ("lon", "longitude", "x") if n in data_array.coords), None)
    if lat_name is None or lon_name is None:
        raise KeyError("Forecast output is missing latitude/longitude coordinates")
    return lat_name, lon_name
