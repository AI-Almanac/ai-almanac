"""Probabilistic skill scores parsed from ROMP's skill-score CSV output.

ROMP writes two CSVs per (model, verification window) when ``probabilistic`` is
enabled, both flat in the job's ``output/`` directory:

``overall_skill_scores_{model}_{window}.csv``
    A header row plus a single data row of domain-pooled scores.

``binned_skill_scores_{model}_{window}.csv``
    One row per lead-time bin (``Days 1-5``, ``Days 6-10``, ...).

These are the only place ROMP persists Brier / RPS / AUC; unlike the
deterministic FAR / MR / MAE fields they never reach a NetCDF, so they are
parsed from CSV rather than discovered through ``list_nc_output_files``.

All functions here are synchronous and intended to be called via
asyncio.to_thread() from the async route handlers in routers/jobs.py.
"""

from __future__ import annotations

import csv
import io
import logging
import re

from pydantic import BaseModel

from .storage import StorageBackend

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSV contract
# ---------------------------------------------------------------------------

# ROMP composes these names as f"{kind}_skill_scores_{model}_{window}.csv" where
# window is "-".join(map(str, verification_window)) — see momp/io/output.py and
# momp/utils/printing.py:tuple_to_str.
#
# The model token itself contains underscores (romp_safe_model_name collapses
# whitespace runs to "_"), so the pattern anchors on the trailing window token
# and lets the model group be greedy. The comma alternative mirrors the legacy
# fallback in StorageBackend.find_nc_output_file.
_SKILL_FILE_RE = re.compile(
    r"^(?P<kind>overall|binned)_skill_scores_"
    r"(?P<model>.+)_"
    r"(?P<window>\d+[-,]\d+)\.csv$"
)

# "Days 1-5" -> (1, 5). ROMP's get_target_bins filters out the "Before day N" /
# "After day N" tail bins before writing, so only this form should appear.
_BIN_LABEL_RE = re.compile(r"^Days\s+(?P<start>\d+)\s*-\s*(?P<end>\d+)$")

# CSV column -> romp.yaml metric id. ROMP only ever persists the fair
# (ensemble-size debiased) variants, so the Fair_ prefix carries no information
# at this layer and is dropped. AUC_ref is the climatology reference and has no
# romp.yaml definition of its own.
_OVERALL_COLUMNS: dict[str, str] = {
    "Fair_Brier_Score": "brier_score",
    "Fair_Brier_Skill_Score": "brier_skill_score",
    "Fair_RPS": "ranked_probability_score",
    "Fair_RPS_Skill_Score": "ranked_probability_skill_score",
    "AUC": "auc",
    "AUC_ref": "auc_ref",
}


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SkillBin(BaseModel):
    """One lead-time bin's scores, from binned_skill_scores_*.csv."""

    bin: str
    label: str
    lead_day_min: int
    lead_day_max: int
    brier_skill_score: float | None
    auc: float | None
    auc_ref: float | None
    brier_score_forecast: float | None
    brier_score_climatology: float | None


class WindowSkillScores(BaseModel):
    model: str
    window: str
    # Keyed by romp.yaml metric id, so the frontend's metric-metadata helpers
    # resolve labels and units without a second mapping.
    overall: dict[str, float | None]
    bins: list[SkillBin]


class JobSkillScores(BaseModel):
    job_id: str
    windows: list[WindowSkillScores]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_float(raw: str | None) -> float | None:
    """Coerce a CSV cell to float, treating blanks and non-numerics as missing.

    pandas writes NaN as an empty string, so blank cells are the normal
    representation of "this bin had no valid samples".
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    # NaN and infinities are not JSON-representable; treat them as missing.
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _parse_bin_label(bin_label: str, clean_label: str) -> tuple[str, int, int] | None:
    """Extract ("1-5", 1, 5) from a "Days 1-5" bin label."""
    match = _BIN_LABEL_RE.match(bin_label.strip())
    if match is None:
        return None
    start = int(match.group("start"))
    end = int(match.group("end"))
    label = clean_label.strip() or f"{start}-{end}"
    return label, start, end


def parse_overall_csv(text: str) -> dict[str, float | None]:
    """Parse overall_skill_scores_*.csv into romp.yaml-keyed scores."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return {}
    row = rows[0]
    return {
        metric_id: _parse_float(row.get(column))
        for column, metric_id in _OVERALL_COLUMNS.items()
        if column in row
    }


def parse_binned_csv(text: str) -> list[SkillBin]:
    """Parse binned_skill_scores_*.csv into lead-time bins, sorted by lead day."""
    bins: list[SkillBin] = []
    for row in csv.DictReader(io.StringIO(text)):
        parsed = _parse_bin_label(row.get("Bin") or "", row.get("clean_bins") or "")
        if parsed is None:
            # "Before day N" / "After day N" tails, or a malformed row.
            continue
        label, start, end = parsed
        bins.append(
            SkillBin(
                bin=(row.get("Bin") or "").strip(),
                label=label,
                lead_day_min=start,
                lead_day_max=end,
                brier_skill_score=_parse_float(row.get("Fair_Brier_Skill_Score")),
                auc=_parse_float(row.get("AUC")),
                auc_ref=_parse_float(row.get("AUC_ref")),
                brier_score_forecast=_parse_float(row.get("Fair_Brier_Score_Forecast")),
                brier_score_climatology=_parse_float(row.get("Fair_Brier_Score_Climatology")),
            )
        )
    bins.sort(key=lambda b: (b.lead_day_min, b.lead_day_max))
    return bins


def _window_sort_key(window: str) -> tuple[int, int, str]:
    """Sort "1-15" before "16-30" numerically rather than lexicographically."""
    parts = window.split("-")
    try:
        return (int(parts[0]), int(parts[1]), window)
    except (IndexError, ValueError):
        return (1 << 30, 1 << 30, window)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_job_skill_scores(job_id: str, storage: StorageBackend) -> JobSkillScores:
    """Collect every skill-score CSV in a job's output directory.

    Returns an empty ``windows`` list for deterministic jobs, which produce no
    skill CSVs at all. That is a normal outcome, not an error — ROMP's
    deterministic and probabilistic paths are mutually exclusive.
    """
    # Group the two CSV kinds by the (model, window) pair encoded in their names.
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for kind, filename in storage.list_result_files(job_id):
        if kind != "output":
            continue
        match = _SKILL_FILE_RE.match(filename)
        if match is None:
            continue
        # Normalize the legacy comma form, matching compute_job_metrics' handling
        # of the verification_window NetCDF attribute.
        window = match.group("window").replace(",", "-")
        key = (match.group("model"), window)
        grouped.setdefault(key, {})[match.group("kind")] = filename

    windows: list[WindowSkillScores] = []
    for (model, window), files in grouped.items():
        overall: dict[str, float | None] = {}
        bins: list[SkillBin] = []

        if overall_name := files.get("overall"):
            text = storage.read_result_text(job_id, "output", overall_name)
            if text:
                overall = parse_overall_csv(text)

        if binned_name := files.get("binned"):
            text = storage.read_result_text(job_id, "output", binned_name)
            if text:
                bins = parse_binned_csv(text)

        if not overall and not bins:
            logger.warning(
                "Skill-score CSVs for job %s model=%s window=%s parsed to nothing",
                job_id,
                model,
                window,
            )
            continue

        windows.append(WindowSkillScores(model=model, window=window, overall=overall, bins=bins))

    windows.sort(key=lambda w: (w.model, _window_sort_key(w.window)))
    return JobSkillScores(job_id=job_id, windows=windows)
