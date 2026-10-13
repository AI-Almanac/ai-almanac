"""Assistant rulesets: the packaged seeds and prompt assembly.

The invariants that used to be asserted against ``llm.SYSTEM_PROMPT`` in
test_chat_prompt.py move here, since the prompt is now assembled from a ruleset
rather than concatenated in Python.
"""

from __future__ import annotations

import pytest

from ai_almanac.server.services import rulesets
from ai_almanac.server.services.guardrails import DEFAULT_GUARDRAILS
from ai_almanac.server.services.rulesets import PromptSection, Ruleset


def builtin() -> Ruleset:
    return rulesets.packaged_ruleset("builtin")


def instructions(scope_kind: str = "benchmark_run_group") -> str:
    return rulesets.build_instructions(builtin(), scope_kind)


# --- prompt assembly ------------------------------------------------------


def _ruleset(*sections: PromptSection) -> Ruleset:
    return Ruleset(id="test", name="Test", prompt_sections=list(sections))


def test_a_scoped_section_reaches_only_its_scope() -> None:
    ruleset = _ruleset(
        PromptSection(key="always", body="EVERYWHERE"),
        PromptSection(key="blend", body="BLEND ONLY", scope_kinds=["blend_setup"]),
    )
    assert "BLEND ONLY" not in rulesets.build_instructions(ruleset, "benchmark_run_group")
    assert "BLEND ONLY" in rulesets.build_instructions(ruleset, "blend_setup")
    assert "EVERYWHERE" in rulesets.build_instructions(ruleset, "benchmark_run_group")


# --- guardrail placeholders ----------------------------------------------


def test_no_unresolved_placeholder_reaches_the_model() -> None:
    for scope in ("benchmark_setup", "benchmark_run_group", "blend_setup", "job_set"):
        assert "{{" not in instructions(scope), scope


def test_the_prose_and_the_enforced_threshold_come_from_one_value(monkeypatch) -> None:
    """The point of moving the thresholds out of the ruleset: an admin who
    retunes one must not leave the assistant quoting the old number, and what
    the assistant says and what the platform enforces are the same value."""
    from ai_almanac.server.services import guardrails as guardrails_module
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "guardrail_blend_member_warn", 5)
    monkeypatch.setattr(settings, "guardrail_min_training_years", 4)

    text = rulesets.build_instructions(builtin(), "blend_setup")
    assert "5 or more members" in text
    assert "Fewer than 4 years cannot train" in text

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


def test_the_builtin_marks_the_caveats_required() -> None:
    caveats = next(s for s in builtin().prompt_sections if s.key == "caveats")
    assert caveats.required


def test_disabling_an_optional_section_drops_only_that_section() -> None:
    ruleset = _ruleset(
        PromptSection(key="kept", body="KEPT", required=True),
        PromptSection(key="dropped", body="DROPPED", enabled=False),
    )
    text = rulesets.build_instructions(ruleset, "blend_setup")
    assert "DROPPED" not in text
    assert "KEPT" in text


# --- the control arm ----------------------------------------------------


def test_the_control_arm_differs_from_the_builtin_in_prose_only() -> None:
    """Denying the control arm tools would confound the comparison it exists
    to make."""
    control = rulesets.packaged_ruleset("unconstrained")
    text = rulesets.build_instructions(control, "blend_setup")
    assert "Interpreting results: caveats" not in text
    assert "Traditional Climatology" not in text
    assert text.strip()
    assert control.tool_policy.deny == []


# --- stored rulesets ------------------------------------------------------


@pytest.mark.asyncio
async def test_seeding_leaves_exactly_the_builtin_active_and_chat_never_lacks_one() -> None:
    """Seeding runs on every startup, so it must be idempotent and keep one
    active row; and chat must not go down when no row is active."""
    from sqlalchemy import text

    from ai_almanac.server.db import get_db

    assert (await rulesets.active_ruleset()).id == "builtin"  # empty table

    await rulesets.seed_packaged_rulesets()
    await rulesets.seed_packaged_rulesets()
    stored = {row.ruleset.id: row.is_active for row in await rulesets.list_rulesets()}
    assert {"builtin", "unconstrained"} <= set(stored)
    assert [ruleset_id for ruleset_id, active in stored.items() if active] == ["builtin"]

    async with get_db() as conn:
        await conn.execute(text("UPDATE assistant_rulesets SET is_active = FALSE"))
    fallback = await rulesets.active_ruleset()
    assert (fallback.id, fallback.source) == ("builtin", "packaged")


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


# --- sections that explain a tool ----------------------------------------


def _code_tool_mentions(available_tools: set[str] | None) -> tuple[bool, bool]:
    text = rulesets.build_instructions(
        builtin(), "benchmark_run_group", available_tools=available_tools
    )
    return "run_code(" in text, "run_code_sandbox(" in text


@pytest.mark.parametrize(
    ("available_tools", "mentions"),
    [
        # A local build drops the code tools; the prompt describing them anyway
        # is how the assistant ended up telling a user its instructions were wrong.
        ({"list_jobs", "get_job_metrics"}, (False, False)),
        ({"run_code_sandbox"}, (False, True)),
        ({"run_code", "run_code_sandbox"}, (True, True)),
        # An admin preview shows every section.
        (None, (True, True)),
    ],
)
def test_only_registered_code_tools_are_explained(
    available_tools: set[str] | None, mentions: tuple[bool, bool]
) -> None:
    assert _code_tool_mentions(available_tools) == mentions
