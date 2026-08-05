"""The guardrail predicates.

These are pure functions, so they are tested directly rather than through a
config path. Enforcement — that a finding with severity "error" actually blocks
a submission on every entry point — is tested in test_guardrail_enforcement.py.
"""

from __future__ import annotations

from ai_almanac.server.services.guardrails import (
    BlendYears,
    Guardrails,
    ModelWindow,
    check_benchmark,
    check_blend,
    error_messages,
    warning_messages,
)


def keys(findings) -> set[str]:
    return {finding.key for finding in findings}


def by_key(findings, key: str):
    return next(finding for finding in findings if finding.key == key)


# --- blend ----------------------------------------------------------------


def test_true_holdout_overlapping_training_is_an_error():
    findings = check_blend(
        BlendYears(
            training=range(2000, 2015),
            cv_holdout=range(2000, 2015),
            true_holdout=[2014, 2015],
        ),
        member_count=2,
    )
    finding = by_key(findings, "true_holdout_overlap")
    assert finding.severity == "error"
    # Names the leaked year, not just the rule, so the user can act on it.
    assert "2014" in finding.message
    assert "2015" not in finding.message.split("A holdout")[0]


def test_training_overlapping_cv_holdout_is_not_flagged():
    """The default split is leave-one-year-out, where training == cv_holdout.

    Flagging it would make the honest default look like a violation.
    """
    years = list(range(2000, 2015))
    findings = check_blend(
        BlendYears(training=years, cv_holdout=years),
        member_count=2,
    )
    assert "true_holdout_overlap" not in keys(findings)
    assert error_messages(findings) == []


def test_short_training_span_warns_without_erroring():
    findings = check_blend(
        BlendYears(training=range(2010, 2014), cv_holdout=range(2010, 2014)),
        member_count=2,
    )
    finding = by_key(findings, "training_years_below_minimum")
    assert finding.severity == "warning"
    assert "4 year" in finding.message
    assert error_messages(findings) == []


def test_no_training_years_does_not_warn_about_the_count():
    """An empty config is incomplete, not statistically unsound — the missing
    field reporting already covers it, and a duplicate warning is noise."""
    findings = check_blend(BlendYears(), member_count=0)
    assert findings == []


def test_third_member_warns_and_never_errors():
    two = check_blend(BlendYears(training=range(2000, 2015)), member_count=2)
    three = check_blend(BlendYears(training=range(2000, 2015)), member_count=3)
    assert "blend_members_at_risk" not in keys(two)
    assert by_key(three, "blend_members_at_risk").severity == "warning"
    assert error_messages(three) == []


def test_small_sample_and_presatellite_years_warn():
    findings = check_blend(
        BlendYears(training=range(1970, 1975), cv_holdout=range(1970, 1975)),
        member_count=2,
    )
    assert by_key(findings, "small_test_sample").severity == "warning"
    presatellite = by_key(findings, "presatellite_years")
    assert presatellite.severity == "warning"
    assert "1978" in presatellite.message


def test_a_long_modern_two_member_blend_is_clean():
    years = list(range(1990, 2015))
    assert check_blend(BlendYears(training=years, cv_holdout=years), member_count=2) == []


def test_thresholds_come_from_the_guardrails_record():
    years = list(range(2010, 2015))
    relaxed = Guardrails(min_training_years=3, small_sample_years=3, blend_member_warn=9)
    assert check_blend(BlendYears(training=years, cv_holdout=years), 3, relaxed) == []


# --- benchmark ------------------------------------------------------------


def test_climatology_fitted_on_the_evaluation_window_warns():
    findings = check_benchmark(
        [
            ModelWindow(
                model_id="aifs",
                eval_start_year=2000,
                eval_end_year=2020,
                clim_start_year=2000,
                clim_end_year=2020,
            )
        ]
    )
    finding = by_key(findings, "in_sample_climatology")
    assert finding.severity == "warning"
    assert "aifs" in finding.message
    assert error_messages(findings) == []


def test_climatology_preceding_the_evaluation_window_is_clean():
    findings = check_benchmark(
        [
            ModelWindow(
                model_id="aifs",
                eval_start_year=2005,
                eval_end_year=2020,
                clim_start_year=1985,
                clim_end_year=2004,
            )
        ]
    )
    assert "in_sample_climatology" not in keys(findings)


def test_an_unknown_bound_makes_the_overlap_rule_unenforceable():
    """Matches how blend_year_coverage treats unregistered year metadata: an
    unknown bound leaves the rule unenforceable rather than violated."""
    findings = check_benchmark(
        [
            ModelWindow(
                model_id="aifs",
                eval_start_year=2000,
                eval_end_year=None,
                clim_start_year=2000,
                clim_end_year=2020,
            )
        ]
    )
    assert "in_sample_climatology" not in keys(findings)


def test_benchmark_sample_size_is_measured_across_models():
    findings = check_benchmark(
        [
            ModelWindow(model_id="aifs", eval_start_year=2018, eval_end_year=2020),
            ModelWindow(model_id="graphcast", eval_start_year=2019, eval_end_year=2021),
        ]
    )
    # 2018-2021 is four distinct years, not two three-year spans.
    assert "4 year" in by_key(findings, "small_test_sample").message


def test_message_helpers_split_on_severity():
    findings = check_blend(
        BlendYears(training=[2020, 2021], cv_holdout=[2020, 2021], true_holdout=[2021]),
        member_count=3,
    )
    assert len(error_messages(findings)) == 1
    assert len(warning_messages(findings)) == len(findings) - 1
