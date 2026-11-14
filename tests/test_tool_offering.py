"""Which tools each kind of conversation is offered, in a deployment with no code sandbox."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import llm, rulesets
from ai_almanac.settings import settings


def _rows(tools: set[str], offered_in: set[str], withheld_in: set[str]) -> list[tuple]:
    return [(kind, tool, True) for tool in sorted(tools) for kind in sorted(offered_in)] + [
        (kind, tool, False) for tool in sorted(tools) for kind in sorted(withheld_in)
    ]


OFFERED = [
    *_rows({"list_areas", "find_country"}, {"benchmark_run_group"}, set()),
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
