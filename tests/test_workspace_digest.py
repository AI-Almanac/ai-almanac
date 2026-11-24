"""The per-turn artifact digest stays bounded as a workspace grows (D-R9)."""

from __future__ import annotations

from ai_almanac.server.services import workspaces


def _item(n: int, group: str = "Figures") -> dict:
    return {
        "id": f"a{n}",
        "label": f"item_{n}",
        "title": f"Item {n}",
        "kind": "figure",
        "group": group,
        "created_at": f"2026-09-25T00:{n:02d}:00",
        "breadcrumb": ["figure.plot_map"],
    }


def test_the_digest_shows_recent_and_referenced_items_and_counts_the_rest() -> None:
    items = [_item(n, "Tables" if n < 10 else "Figures") for n in range(50)]
    digest = workspaces.prompt_listing(items, referenced={"a3"})

    shown = {line.split(" — ")[0][2:] for line in digest.splitlines() if line.startswith("- item_")}
    assert shown == {f"item_{n}" for n in range(30, 50)} | {"item_3"}
    assert "holds 50 artifacts (Figures: 40, Tables: 10)" in digest
    assert "29 more — call list_artifacts" in digest


def test_a_small_workspace_is_listed_in_full() -> None:
    digest = workspaces.prompt_listing([_item(1), _item(2)])
    assert "item_1" in digest and "item_2" in digest and "more" not in digest
    assert workspaces.prompt_listing([]).strip() == "The workspace has no artifacts yet."


def test_the_digest_marks_files_of_analyses_the_user_hid() -> None:
    items = [_item(1) | {"turn_id": "turn-wrong"}, _item(2) | {"turn_id": "turn-right"}]

    digest = workspaces.prompt_listing(items, hidden_turns={"turn-wrong"})
    lines = {
        line.split(" — ")[0][2:]: line for line in digest.splitlines() if line.startswith("- ")
    }

    assert lines["item_1"].endswith("(hidden by the user)")
    assert not lines["item_2"].endswith("(hidden by the user)")
    assert workspaces.HIDDEN_BY_USER_NOTE in digest
    assert workspaces.HIDDEN_BY_USER_NOTE not in workspaces.prompt_listing(items)
