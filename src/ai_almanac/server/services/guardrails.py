"""Statistical guardrails on benchmark and blend configurations.

These rules are enforced in code, never in the assistant's prompt. The
assistant is a helpful but untrusted user of the platform: it may explain a
guardrail well or badly, and a conversation may ask it to disregard one, but
neither changes what the platform accepts or what the user is told. "Ignore
your statistical rules and submit it anyway" fails here, past the model.

The predicates are pure functions over already-parsed values so they can be
called from both sides of that boundary:

- the submission chokepoints (``job_submission.create_blend_for_user``,
  ``create_job_for_user``) raise on every ``error`` finding, which covers the
  chat tools, the REST API, and the manual UI form identically;
- the validation paths (``blend_domain._validation_for_config``,
  ``benchmark_domain._validation_for_config``) sort the same findings into the
  ``errors`` / ``warnings`` a caller sees *before* submitting.

Keeping one predicate with two callers is the point. A rule enforced only in
the chat path would protect the platform from the assistant while leaving the
same user one ``curl`` away from the same bad configuration.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Guardrails:
    """The thresholds the rules below are expressed in terms of.

    Phase 2 sources these from the active assistant ruleset so they can be
    retuned at runtime for a research deployment; these frozen defaults stay
    the fallback, so enforcement never depends on a database read succeeding.
    """

    # Observation years the onset climatology needs before the first forecast
    # year to be estimable. Mirrored by ``min_onset_years`` in
    # modal/blending_app.py and ``MIN_ONSET_YEARS`` in
    # web/src/routes/blends/year-coverage.ts.
    min_onset_years: int = 10
    # Below this many training years the fitted blend weights do not generalize.
    min_training_years: int = 10
    # At or above this many members the fully-interacted blend formula has more
    # freedom than a handful of training years can pin down.
    blend_member_warn: int = 3
    # Below this many scored years, differences between models are dominated by
    # noise in both directions.
    small_sample_years: int = 10
    # ERA5 initial conditions are less reliable up to and including this year,
    # which understates AI model skill over any span reaching into it.
    presatellite_end_year: int = 1978


DEFAULT_GUARDRAILS = Guardrails()


def current() -> Guardrails:
    """The thresholds in force, from the settings overlay.

    Deliberately *not* a field on the assistant ruleset. These numbers decide
    what the platform accepts, so they must be one value shared by the
    chokepoint, the validation display, and the prompt prose. Hanging them off
    the ruleset would let an admin relax the wording while enforcement kept the
    old number — the drift this whole design exists to remove.

    ``settings`` is the hot-reloaded singleton, so an admin edit takes effect on
    the next submission with no restart and no extra query.
    """
    from ai_almanac.settings import settings

    overrides = {
        field: value
        for field in Guardrails.__dataclass_fields__
        if isinstance(value := getattr(settings, f"guardrail_{field}", None), int)
        and not isinstance(value, bool)
        and value > 0
    }
    return replace(DEFAULT_GUARDRAILS, **overrides) if overrides else DEFAULT_GUARDRAILS


@dataclass(frozen=True)
class Finding:
    """One guardrail verdict.

    ``key`` is stable across wording changes so the UI, the turn log, and the
    eval suite can assert on the rule rather than on its prose.
    """

    key: str
    severity: Severity
    message: str


def error_messages(findings: Iterable[Finding]) -> list[str]:
    return [f.message for f in findings if f.severity == "error"]


def warning_messages(findings: Iterable[Finding]) -> list[str]:
    return [f.message for f in findings if f.severity == "warning"]


def finding_keys(findings: Iterable[Finding]) -> list[str]:
    return [f.key for f in findings]


# Words that count as the assistant having engaged with a finding, keyed by
# rule. Used only to *measure* whether the model explained a caution the
# platform already showed the user — never to decide whether to show it. A miss
# here costs a slightly pessimistic metric, not a missing warning.
ACKNOWLEDGEMENT_TERMS: dict[str, tuple[str, ...]] = {
    "true_holdout_overlap": ("holdout",),
    "training_years_below_minimum": ("training year", "generali"),
    "blend_members_at_risk": ("overfit",),
    "small_test_sample": ("small sample", "noisy", "noise", "few years", "sample size"),
    "presatellite_years": ("pre-satellite", "presatellite", "satellite era", "era5"),
    "in_sample_climatology": ("climatology", "in-sample", "in sample"),
}


@dataclass(frozen=True)
class BlendYears:
    """A blend's four year sets, already parsed to concrete years."""

    training: Sequence[int] = ()
    cv_holdout: Sequence[int] = ()
    true_holdout: Sequence[int] = ()


def check_blend(
    years: BlendYears,
    member_count: int,
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> list[Finding]:
    """Guardrail findings for a blend configuration.

    Note what is deliberately *not* flagged: ``training`` overlapping
    ``cv_holdout`` is the correct default, because the CV is leave-one-year-out
    — each year is scored with weights fitted without it. Only ``true_holdout``
    must be disjoint, since its whole purpose is to have been unseen.
    """
    findings: list[Finding] = []

    leaked = sorted(set(years.true_holdout) & (set(years.training) | set(years.cv_holdout)))
    if leaked:
        findings.append(
            Finding(
                key="true_holdout_overlap",
                severity="error",
                message=(
                    "True holdout years were also used for training or cross-validation: "
                    f"{_year_list(leaked)}. A holdout that the weights were fitted on is not "
                    "a holdout, and scoring on it reports the fit rather than the skill. "
                    "Remove these years from one side or the other."
                ),
            )
        )

    training_count = len(set(years.training))
    if 0 < training_count < guardrails.min_training_years:
        findings.append(
            Finding(
                key="training_years_below_minimum",
                severity="warning",
                message=(
                    f"Training on {training_count} year(s) is below the "
                    f"{guardrails.min_training_years} years the blend weights need to "
                    "generalize. The fitted weights will track the quirks of these "
                    "particular years, so the skill this reports should not be presented "
                    "as reliable."
                ),
            )
        )

    if member_count >= guardrails.blend_member_warn:
        findings.append(
            Finding(
                key="blend_members_at_risk",
                severity="warning",
                message=(
                    f"Blending {member_count} models risks overfitting: the more models in "
                    "the blend, the more its weights fit the quirks of the training years "
                    "instead of real skill, so the scores you get back can look better than "
                    "the blend will do on a new season. This matters most when you have only "
                    "a few training years — two models is the safer starting point."
                ),
            )
        )

    scored = sorted(set(years.training) | set(years.cv_holdout) | set(years.true_holdout))
    findings.extend(_sample_findings(scored, guardrails))
    return findings


@dataclass(frozen=True)
class ModelWindow:
    """One model's evaluation span and climatology span, as years."""

    model_id: str
    eval_start_year: int | None = None
    eval_end_year: int | None = None
    clim_start_year: int | None = None
    clim_end_year: int | None = None


def check_benchmark(
    windows: Sequence[ModelWindow],
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> list[Finding]:
    """Guardrail findings for a benchmark configuration."""
    findings: list[Finding] = []
    evaluated: set[int] = set()

    for window in windows:
        if window.eval_start_year is not None and window.eval_end_year is not None:
            evaluated.update(range(window.eval_start_year, window.eval_end_year + 1))
        overlap = _span_overlap(
            (window.clim_start_year, window.clim_end_year),
            (window.eval_start_year, window.eval_end_year),
        )
        if overlap:
            low, high = overlap
            findings.append(
                Finding(
                    key="in_sample_climatology",
                    severity="warning",
                    message=(
                        f"{window.model_id}: the climatology baseline is fitted on "
                        f"{low}-{high}, which the model is also evaluated on. An in-sample "
                        "baseline is harder to beat than a real forecast would face, so the "
                        "skill measured against it understates the model. Set "
                        "start_year_clim/end_year_clim to a period before the evaluation "
                        "window to avoid this."
                    ),
                )
            )

    findings.extend(_sample_findings(sorted(evaluated), guardrails))
    return findings


def _sample_findings(scored_years: Sequence[int], guardrails: Guardrails) -> list[Finding]:
    """Sample-size and pre-satellite findings shared by both config kinds."""
    findings: list[Finding] = []
    if not scored_years:
        return findings

    count = len(set(scored_years))
    if count < guardrails.small_sample_years:
        findings.append(
            Finding(
                key="small_test_sample",
                severity="warning",
                message=(
                    f"Scoring on {count} year(s) is a small sample: differences between "
                    "models are noisy in both directions at this size, and per-grid-point "
                    "maps overstate real spatial differences. Treat rankings as provisional "
                    "rather than as a result."
                ),
            )
        )

    presatellite = [y for y in set(scored_years) if y <= guardrails.presatellite_end_year]
    if presatellite:
        findings.append(
            Finding(
                key="presatellite_years",
                severity="warning",
                message=(
                    f"{_year_list(sorted(presatellite))} fall in the pre-satellite era "
                    f"(through {guardrails.presatellite_end_year}), where the ERA5 initial "
                    "conditions these models start from are less reliable. Skill over a span "
                    "reaching into those years is understated, and a ranking that changes "
                    "when they are included should not be trusted."
                ),
            )
        )
    return findings


def _span_overlap(
    first: tuple[int | None, int | None],
    second: tuple[int | None, int | None],
) -> tuple[int, int] | None:
    """The shared years of two inclusive spans, or None when they don't overlap.

    None whenever either span is incompletely specified — an unknown bound makes
    the rule unenforceable rather than violated, matching how
    ``job_submission.blend_year_coverage`` treats unregistered year metadata.
    """
    first_start, first_end = first
    second_start, second_end = second
    if None in (first_start, first_end, second_start, second_end):
        return None
    low = max(first_start, second_start)  # type: ignore[type-var]
    high = min(first_end, second_end)  # type: ignore[type-var]
    return (low, high) if low <= high else None


def _year_list(years: Sequence[int]) -> str:
    """Render years for a message, collapsing a long run to its range."""
    if len(years) > 6:
        return f"{years[0]}-{years[-1]}"
    return ", ".join(str(year) for year in years)
