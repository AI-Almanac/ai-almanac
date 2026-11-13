"""Which tools each kind of conversation is offered, in a deployment with no code sandbox."""

from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from ai_almanac.server.services import llm, rulesets
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.settings import settings

PAGE_CHAT = "benchmark_setup"
DRAFT_TOOLS = {
    "get_benchmark_config",
    "update_benchmark_config",
    "validate_benchmark_config",
    "get_blend_config",
    "update_blend_config",
    "validate_blend_config",
}


def _rows(tools: set[str], offered_in: set[str], withheld_in: set[str]) -> list[tuple]:
    return [(kind, tool, True) for tool in sorted(tools) for kind in sorted(offered_in)] + [
        (kind, tool, False) for tool in sorted(tools) for kind in sorted(withheld_in)
    ]


OFFERED = [
    *_rows({"list_areas", "find_country"}, {"workspace", "benchmark_run_group"}, set()),
    *_rows({"validate_benchmark", "validate_blend"}, {"workspace"}, {PAGE_CHAT}),
    *_rows({"submit_benchmark", "rerun_job"}, {"workspace", PAGE_CHAT}, set()),
    *_rows(
        {"submit_blend", "list_regions", "list_datasets", "list_models", "list_blend_models"},
        {"workspace"},
        set(),
    ),
    *_rows({"list_artifacts", "describe_provenance"}, {"workspace"}, set()),
    *_rows(DRAFT_TOOLS, set(), {"workspace"}),
    *_rows({"update_benchmark_config"}, {PAGE_CHAT}, set()),
    *_rows({"cancel_run"}, {"workspace", PAGE_CHAT}, {"general"}),
    *_rows({"open_panel", "show"}, {"workspace"}, {PAGE_CHAT}),
]


@pytest.fixture(autouse=True)
def _no_code_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "code_sandbox", "off")


@pytest.mark.parametrize(("scope_kind", "tool", "offered"), OFFERED)
def test_each_kind_of_conversation_is_offered_its_own_tools(
    scope_kind: str, tool: str, offered: bool
) -> None:
    names = llm.offered_tool_names(rulesets.packaged_ruleset("builtin"), scope_kind)

    assert (tool in names) is offered


@pytest.mark.asyncio
async def test_a_workspace_agent_is_given_and_told_of_exactly_the_tools_it_is_offered() -> None:
    builtin = rulesets.packaged_ruleset("builtin")
    scope = ChatScope(kind="workspace", key="ws-1", job_ids=[])
    model = TestModel(call_tools=[], custom_output_text="ready")
    agent = llm._build_agent(scope, builtin)

    with agent.override(model=model):
        await agent.run("hi", deps=llm.ChatDeps(user_id="u", session_id="s", scope=scope))

    given = {tool.name for tool in model.last_model_request_parameters.function_tools}
    assert given == set(llm.offered_tool_names(builtin, "workspace"))
    prompt = llm._instructions_for_ruleset(builtin, scope, given)
    assert "validate_benchmark(spec)" in prompt
    assert "update_benchmark_config" not in prompt and "run_code" not in prompt
