"""Short names for a workspace's analyses: the results one chat turn made.

An analysis is named by what it shows. The assistant says that when it makes a
result (the name or title it gives the file), so those words name the analysis.
Only when it gave none does a small model call name the results from the question
and their file names, so the results panel shows "Amhara zones: FuXi onset, July
2024" instead of repeating the question. Naming runs after the reply, off the
turn's path; a failure leaves the analysis showing the question.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence

import sqlalchemy as sa
from pydantic_ai import Agent

from ai_almanac.server import tables
from ai_almanac.server.db import get_db

from . import workspaces

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "You name a set of results in a weather forecaster's workspace. Reply with the name "
    "only: 3 to 8 plain words a forecaster would use, naming the place, the forecast or "
    "model, and what was worked out, e.g. 'Amhara zones: FuXi onset, July 2024'. No "
    "quotes, no trailing full stop, no file names, tool names or codes like adm2."
)
_MARKUP = re.compile(r"[*_#`]+")

# Naming tasks run after the turn has returned; held here so they are not collected.
_PENDING: set[asyncio.Task] = set()


def naming_prompt(question: str | None, titles: Sequence[str]) -> str:
    asked = f"The forecaster asked: {question.strip()}\n" if question and question.strip() else ""
    return f"{asked}The results made: {'; '.join(titles)}\nName these results."


def parse_name(text: str) -> str | None:
    """The first line of the reply, without markup, quotes or a trailing full stop;
    None when that leaves nothing usable as a name."""
    line = next((line for line in text.splitlines() if line.strip()), "")
    name = _MARKUP.sub("", line).strip(" \"'“”‘’.")
    if not name or len(name) > workspaces.MAX_TITLE_LENGTH:
        return None
    return name


def name_from_results(given: Sequence[str]) -> str | None:
    """An analysis's name from the names its results were given, the first result
    leading; None when none was given or the first is too long to be a name."""
    distinct = list(dict.fromkeys(title.strip() for title in given if title.strip()))
    if not distinct or len(distinct[0]) > workspaces.MAX_TITLE_LENGTH:
        return None
    lead, others = distinct[0], len(distinct) - 1
    counted = f"{lead} (+{others} more)"
    return counted if others and len(counted) <= workspaces.MAX_TITLE_LENGTH else lead


async def given_titles(workspace_id: str, turn_id: str) -> list[str]:
    """The names given to the results (not supporting files) a turn made, oldest first.
    A result left to be named after its file is not among them."""
    items, artifacts, steps = tables.workspace_items, tables.artifacts, tables.provenance_steps
    async with get_db() as conn:
        result = await conn.execute(
            sa.select(items.c.title)
            .join(artifacts, artifacts.c.id == items.c.artifact_id)
            .join(steps, steps.c.id == artifacts.c.provenance_step_id)
            .where(
                items.c.workspace_id == workspace_id,
                steps.c.turn_id == turn_id,
                items.c.title.is_not(None),
                sa.func.coalesce(items.c.supporting, sa.false()).is_(sa.false()),
            )
            .order_by(items.c.added_at)
        )
        return list(result.scalars())


async def _name_from_model(
    workspace_id: str, turn_id: str, question: str | None, model: object
) -> str | None:
    titles = await workspaces.turn_file_titles(workspace_id, turn_id)
    if not titles:
        return None
    agent = Agent(model, instructions=INSTRUCTIONS, output_type=str)
    result = await agent.run(naming_prompt(question, titles))
    return parse_name(result.output)


async def name_turn(
    workspace_id: str, turn_id: str, question: str | None, model: object | None
) -> dict | None:
    """Name the results ``turn_id`` added, unless it added none or someone already
    named them: by the names the assistant gave them, else by asking ``model``
    (None: leave it unnamed). Returns the analysis it named."""
    name = name_from_results(await given_titles(workspace_id, turn_id))
    if name is None and model is not None:
        name = await _name_from_model(workspace_id, turn_id, question, model)
    if name is None:
        return None
    return await workspaces.name_analysis(workspace_id, turn_id, name, "assistant")


def name_turn_later(
    workspace_id: str, turn_id: str, question: str | None, model: object | None
) -> None:
    task = asyncio.create_task(_name_quietly(workspace_id, turn_id, question, model))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def _name_quietly(
    workspace_id: str, turn_id: str, question: str | None, model: object | None
) -> None:
    try:
        await name_turn(workspace_id, turn_id, question, model)
    except Exception:
        logger.warning("Could not name the analysis for turn %s", turn_id, exc_info=True)
