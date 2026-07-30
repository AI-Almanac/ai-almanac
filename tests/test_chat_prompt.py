"""Chat system-prompt composition: result-interpretation caveats and blend guidance."""

import pytest

from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.llm import (
    BLEND_GUIDANCE,
    CAVEATS_HEADING,
    RESULT_INTERPRETATION_CAVEATS,
    SYSTEM_PROMPT,
    _instructions_for_scope,
)


def test_builtin_prompt_carries_the_interpretation_caveats() -> None:
    assert CAVEATS_HEADING in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.count(CAVEATS_HEADING) == 1
    assert "## Approach" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.index(CAVEATS_HEADING) < SYSTEM_PROMPT.index("## Approach")


@pytest.mark.parametrize(
    "fact",
    ["1965-1978", "10 test years", "trained or fine-tuned", "operational skill", "the maps"],
)
def test_caveats_cover_each_documented_point(fact: str) -> None:
    assert fact in RESULT_INTERPRETATION_CAVEATS


def test_caveats_are_framed_as_conditional_not_boilerplate() -> None:
    assert "not boilerplate" in RESULT_INTERPRETATION_CAVEATS
    assert "do not recite them on every answer" in RESULT_INTERPRETATION_CAVEATS
    assert "Raise the ones" in RESULT_INTERPRETATION_CAVEATS


def test_admin_override_keeps_the_caveats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai_almanac.settings.settings.chat_system_prompt", "You are a terse weather bot."
    )
    prompt = _instructions_for_scope(ChatScope(kind="benchmark_setup", key="setup"))
    assert prompt.startswith("You are a terse weather bot.")
    assert RESULT_INTERPRETATION_CAVEATS.strip() in prompt


def test_admin_override_that_already_has_caveats_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = f"{SYSTEM_PROMPT}\n\nAlways answer in French."
    monkeypatch.setattr("ai_almanac.settings.settings.chat_system_prompt", override)
    prompt = _instructions_for_scope(ChatScope(kind="benchmark_setup", key="setup"))
    assert prompt.count(CAVEATS_HEADING) == 1
    assert prompt.endswith("Always answer in French.")


def _blend_results_docstring() -> str:
    from ai_almanac.server.services.llm import _blend_toolset

    tool = _blend_toolset().tools["get_blend_results"]
    return tool.description or tool.function.__doc__ or ""


@pytest.mark.parametrize(
    "text",
    [SYSTEM_PROMPT, BLEND_GUIDANCE, RESULT_INTERPRETATION_CAVEATS, _blend_results_docstring()],
)
def test_no_stale_baseline_terminology(text: str) -> None:
    assert "unconditional climatology" not in text.lower()


def test_retired_labels_appear_only_as_retired_in_the_prompt() -> None:
    retired = "Climatology (unconditional)"
    assert retired not in BLEND_GUIDANCE
    assert retired not in _blend_results_docstring()
    assert f'The retired labels "{retired}"' in SYSTEM_PROMPT


def test_baselines_use_the_current_display_names() -> None:
    assert "Traditional Climatology" in SYSTEM_PROMPT
    assert "Conditional Climatology" in SYSTEM_PROMPT


def test_blend_results_docstring_scores_against_traditional_climatology() -> None:
    doc = _blend_results_docstring()
    assert "against Traditional Climatology" in doc
    assert "Conditional Climatology" in doc


def test_blend_scope_appends_overfitting_and_sample_size_cautions() -> None:
    prompt = _instructions_for_scope(ChatScope(kind="blend_setup", key="blend"))
    assert BLEND_GUIDANCE in prompt
    assert "Three or more members" in BLEND_GUIDANCE
    assert "overfitting" in BLEND_GUIDANCE
    assert "Fewer than ten training years" in BLEND_GUIDANCE
