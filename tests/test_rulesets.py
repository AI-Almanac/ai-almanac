"""Assistant rulesets: the packaged seeds and prompt assembly.

The invariants that used to be asserted against ``llm.SYSTEM_PROMPT`` in
test_chat_prompt.py move here, since the prompt is now assembled from a ruleset
rather than concatenated in Python.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_almanac.server.services import rulesets
from ai_almanac.server.services.guardrails import DEFAULT_GUARDRAILS
from ai_almanac.server.services.rulesets import PromptSection, Ruleset


def builtin() -> Ruleset:
    return rulesets.packaged_ruleset("builtin")


def instructions(scope_kind: str = "benchmark_run_group") -> str:
    return rulesets.build_instructions(builtin(), scope_kind)


# --- packaged seeds -------------------------------------------------------


def test_both_packaged_rulesets_load() -> None:
    ids = {ruleset.id for ruleset in rulesets.packaged_rulesets()}
    assert {"builtin", "unconstrained"} <= ids
    assert all(ruleset.source == "packaged" for ruleset in rulesets.packaged_rulesets())


def test_builtin_has_the_expected_sections_in_order() -> None:
    assert [section.key for section in builtin().prompt_sections] == [
        "domain",
        "caveats",
        "approach",
        "code_execution",
        "output_style",
        "blend_guidance",
    ]


# --- prompt assembly ------------------------------------------------------


def test_caveats_appear_once_and_before_the_approach_rules() -> None:
    text = instructions()
    assert text.count("Interpreting results: caveats") == 1
    assert text.index("Interpreting results: caveats") < text.index("## Approach")


def test_every_documented_caveat_is_present() -> None:
    text = instructions()
    for caveat in (
        "Training overlap",
        "Pre-satellite era",
        "Small samples",
        "The trilemma",
        "ERA5 versus operational initial conditions",
        "Do not over-read the maps",
    ):
        assert caveat in text, caveat


def test_caveats_are_framed_conditionally_not_as_boilerplate() -> None:
    text = instructions()
    assert "They are not boilerplate" in text
    assert "never open with them" in text
    assert "do not recite them on every answer" in text


def test_blend_guidance_is_scoped_to_blend_setup() -> None:
    assert "risk of overfitting" not in instructions("benchmark_run_group")
    assert "risk of overfitting" in instructions("blend_setup")


def test_retired_labels_survive_only_as_retired() -> None:
    """`unc_` means unconditional, not uncalibrated, and a bare "Climatology"
    used to mean the conditional baseline. The prompt names both — but only to
    forbid them — and must never use them as a live label.
    """
    text = instructions("blend_setup")
    assert 'The retired labels "Climatology (unconditional)"' in text
    assert "unconditional climatology" not in text.lower()
    assert "Traditional Climatology" in text
    assert "Conditional Climatology" in text


def test_the_blend_guidance_section_does_not_reuse_a_retired_label() -> None:
    blend = next(s for s in builtin().prompt_sections if s.key == "blend_guidance")
    assert "Climatology (unconditional)" not in blend.body


# --- guardrail placeholders ----------------------------------------------


def test_no_unresolved_placeholder_reaches_the_model() -> None:
    for scope in ("benchmark_setup", "benchmark_run_group", "blend_setup", "job_set"):
        assert "{{" not in instructions(scope), scope


def test_thresholds_render_from_the_guardrail_defaults() -> None:
    text = instructions("blend_setup")
    assert f"roughly {DEFAULT_GUARDRAILS.small_sample_years} test years" in text
    assert f"Fewer than {DEFAULT_GUARDRAILS.min_training_years} years cannot train" in text
    assert f"1965-{DEFAULT_GUARDRAILS.presatellite_end_year}" in text
    assert f"{DEFAULT_GUARDRAILS.blend_member_warn} or more members" in text


def test_retuning_a_threshold_moves_the_prose_with_it(monkeypatch) -> None:
    """The reason the numbers are placeholders: an admin who relaxes a threshold
    must not leave the assistant quoting the old one.

    The threshold is read from the settings overlay, which is the same value the
    submission chokepoint enforces — so the prose and the check cannot disagree.
    """
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "guardrail_min_training_years", 4)
    text = rulesets.build_instructions(builtin(), "blend_setup")
    assert "Fewer than 4 years cannot train" in text
    assert "Fewer than 10 years cannot train" not in text


def test_the_prose_and_the_enforced_threshold_come_from_one_value(monkeypatch) -> None:
    """The point of moving the thresholds out of the ruleset: what the assistant
    says and what the platform does are the same number."""
    from ai_almanac.server.services import guardrails as guardrails_module
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "guardrail_blend_member_warn", 5)

    text = rulesets.build_instructions(builtin(), "blend_setup")
    assert "5 or more members" in text

    findings = guardrails_module.check_blend(
        guardrails_module.BlendYears(training=range(2000, 2015)),
        member_count=4,
        guardrails=guardrails_module.current(),
    )
    assert "blend_members_at_risk" not in {f.key for f in findings}


def test_an_unknown_placeholder_is_left_visible_rather_than_raising() -> None:
    """A typo should show up in the previewed prompt, not take chat down at
    request time."""
    body = rulesets.render_section("see {{no_such_threshold}}", DEFAULT_GUARDRAILS)
    assert body == "see {{no_such_threshold}}"


# --- section toggling ----------------------------------------------------


def test_a_required_section_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError, match="required"):
        PromptSection(key="caveats", body="...", required=True, enabled=False)


def test_the_builtin_marks_the_caveats_required() -> None:
    caveats = next(s for s in builtin().prompt_sections if s.key == "caveats")
    assert caveats.required


def test_disabling_an_optional_section_drops_it() -> None:
    ruleset = builtin()
    sections = [
        s.model_copy(update={"enabled": False}) if s.key == "output_style" else s
        for s in ruleset.prompt_sections
    ]
    trimmed = ruleset.model_copy(update={"prompt_sections": sections})
    assert "## Output style" not in rulesets.build_instructions(trimmed, "blend_setup")
    # The required section is untouched by an unrelated edit.
    assert "Interpreting results: caveats" in rulesets.build_instructions(trimmed, "blend_setup")


# --- the control arm ----------------------------------------------------


def test_unconstrained_strips_the_rules_but_stays_a_valid_ruleset() -> None:
    control = rulesets.packaged_ruleset("unconstrained")
    text = rulesets.build_instructions(control, "blend_setup")
    assert "Interpreting results: caveats" not in text
    assert "Traditional Climatology" not in text
    assert text.strip()


def test_unconstrained_withholds_no_tools() -> None:
    """The control arm differs in prose only. Denying it tools would confound
    the comparison it exists to make."""
    assert rulesets.packaged_ruleset("unconstrained").tool_policy.deny == []


# --- stored rulesets ------------------------------------------------------


@pytest.mark.asyncio
async def test_seeding_installs_the_packaged_rows_and_activates_the_builtin() -> None:
    await rulesets.seed_packaged_rulesets()

    stored = {row.ruleset.id: row.is_active for row in await rulesets.list_rulesets()}
    assert {"builtin", "unconstrained"} <= set(stored)
    assert stored["builtin"] is True
    assert stored["unconstrained"] is False


@pytest.mark.asyncio
async def test_seeding_is_idempotent_and_keeps_one_active() -> None:
    """It runs on every startup, so a second pass must not create a duplicate or
    a second active row — the partial unique index would reject the latter."""
    await rulesets.seed_packaged_rulesets()
    await rulesets.seed_packaged_rulesets()

    active = [row.ruleset.id for row in await rulesets.list_rulesets() if row.is_active]
    assert active == ["builtin"]


@pytest.mark.asyncio
async def test_activating_moves_the_active_flag() -> None:
    await rulesets.seed_packaged_rulesets()
    other = rulesets.next_version(builtin(), "builtin-flag", "Built-in, flagged")
    await rulesets.save_ruleset(other, created_by="admin@example.com")
    await rulesets.activate_ruleset("builtin-flag")

    assert (await rulesets.active_ruleset()).id == "builtin-flag"
    active = [row.ruleset.id for row in await rulesets.list_rulesets() if row.is_active]
    assert active == ["builtin-flag"]

    await rulesets.activate_ruleset("builtin")


@pytest.mark.asyncio
async def test_activating_an_unknown_ruleset_raises() -> None:
    with pytest.raises(KeyError):
        await rulesets.activate_ruleset("no-such-ruleset")


@pytest.mark.asyncio
async def test_activating_the_comparison_control_raises() -> None:
    """`activatable: false` in the YAML is enforced, not advisory."""
    await rulesets.seed_packaged_rulesets()
    before = await rulesets.active_ruleset()

    with pytest.raises(ValueError):
        await rulesets.activate_ruleset("unconstrained")

    assert (await rulesets.active_ruleset()).id == before.id


@pytest.mark.asyncio
async def test_a_saved_edit_round_trips_through_the_database() -> None:
    await rulesets.seed_packaged_rulesets()
    edited = rulesets.next_version(builtin(), "builtin-v2", "Built-in, terser")
    edited = edited.model_copy(
        update={
            "tool_policy": rulesets.ToolPolicy(deny=["run_code_sandbox"]),
            "model_settings": {"temperature": 0.2},
        }
    )
    await rulesets.save_ruleset(edited, created_by="admin@example.com")

    loaded = await rulesets.get_ruleset("builtin-v2")
    assert loaded is not None
    assert loaded.version == builtin().version + 1
    assert loaded.tool_policy.deny == ["run_code_sandbox"]
    assert loaded.model_settings == {"temperature": 0.2}
    assert [s.key for s in loaded.prompt_sections] == [s.key for s in builtin().prompt_sections]


@pytest.mark.asyncio
async def test_saving_onto_a_packaged_id_is_refused() -> None:
    """Packaged rows are rewritten from YAML on every startup. Accepting the save
    would look like it worked and then silently revert on the next restart, so it
    has to fail loudly and point at cloning instead."""
    with pytest.raises(rulesets.PackagedRulesetIdError, match="Clone it"):
        await rulesets.save_ruleset(builtin())


@pytest.mark.asyncio
async def test_a_cloned_edit_survives_reseeding() -> None:
    """The property the rejection above protects: an edit kept under its own id
    is still there after a restart reseeds the packaged rows."""
    await rulesets.seed_packaged_rulesets()
    edited = rulesets.next_version(builtin(), "builtin-kept", "Kept")
    await rulesets.save_ruleset(edited.model_copy(update={"description": "MY EDIT"}))

    await rulesets.seed_packaged_rulesets()  # what a restart does

    survived = await rulesets.get_ruleset("builtin-kept")
    assert survived is not None
    assert survived.description == "MY EDIT"


@pytest.mark.asyncio
async def test_the_active_ruleset_falls_back_to_the_packaged_builtin() -> None:
    """Chat must not go down because the table is empty or a row was archived."""
    from sqlalchemy import text

    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        await conn.execute(text("UPDATE assistant_rulesets SET is_active = FALSE"))

    fallback = await rulesets.active_ruleset()
    assert fallback.id == "builtin"
    assert fallback.source == "packaged"

    await rulesets.seed_packaged_rulesets()
