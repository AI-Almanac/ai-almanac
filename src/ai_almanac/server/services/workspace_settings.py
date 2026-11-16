"""Workspace settings: the defaults that frame every run in a workspace (F0).

An explicit value in a request beats a workspace setting, which beats the platform
default. Jobs freeze the values they resolved, so a changed setting never changes a
past result and settings keep no history.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ai_almanac.server import tables
from ai_almanac.server.db import get_db

from .focus_area import FocusArea, FocusUnits, area_label, country_of
from .seasons import SeasonWindow, default_season, spoken_date


class WorkspaceSettings(BaseModel):
    """Every field is optional; one left unset falls back to the platform default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    season: SeasonWindow | None = None
    # Where runs here are scored unless a run names its own area: a box, or places.
    study_area: FocusArea | None = None

    @field_validator("study_area")
    @classmethod
    def _places_name_their_country(cls, area: FocusArea | None) -> FocusArea | None:
        if isinstance(area, FocusUnits) and not area.iso:
            raise ValueError(
                "places need their country's three-letter code as iso; a workspace has no "
                "region to find them in"
            )
        return area


class InvalidWorkspaceSettings(ValueError):
    """A settings write that does not parse; the message names each problem."""


# --- functional core ----------------------------------------------------------


def parse_settings(raw_json: str | None) -> WorkspaceSettings:
    """The stored settings; NULL means none.

    Keys this version does not know are dropped rather than refused: they were
    written by a newer release, and a rollback must still open the workspace.
    """
    stored = json.loads(raw_json) if raw_json else {}
    known = {key: value for key, value in stored.items() if key in WorkspaceSettings.model_fields}
    return WorkspaceSettings.model_validate(known)


def _plain_problem(error: Mapping) -> str:
    where = ".".join(str(part) for part in error["loc"])
    if error["type"] == "extra_forbidden":
        return f"unknown setting {where}"
    message = error["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


def parse_patch(patch: Mapping[str, object]) -> WorkspaceSettings:
    """A settings change from outside; the fields it names are the ones to change."""
    try:
        return WorkspaceSettings.model_validate(patch)
    except ValidationError as exc:
        problems = "; ".join(_plain_problem(error) for error in exc.errors())
        raise InvalidWorkspaceSettings(f"Invalid workspace settings: {problems}") from exc


def apply_patch(current: WorkspaceSettings, patch: WorkspaceSettings) -> WorkspaceSettings:
    """Each field the patch names replaces the current one (null clears it); the rest stay."""
    return current.model_copy(
        update={name: getattr(patch, name) for name in patch.model_fields_set}
    )


def effective(settings: WorkspaceSettings) -> WorkspaceSettings:
    """What runs in the workspace use: an unset season filled with the one usual where
    the workspace works, else the fallback season."""
    return settings.model_copy(
        update={"season": settings.season or default_season(country_of(settings.study_area))}
    )


def serialize(settings: WorkspaceSettings) -> str:
    return settings.model_dump_json(exclude_none=True)


def describe_season(season: SeasonWindow) -> str:
    return (
        f"onset searched from {spoken_date(season.onset_search_start)}, "
        f"reference onset {spoken_date(season.reference_onset)}, "
        f"last issue date {spoken_date(season.last_issue_date)}"
    )


def describe_set_season(settings: WorkspaceSettings) -> str:
    if settings.season is None:
        return f"Season: none set; platform defaults apply: {describe_season(SeasonWindow())}."
    return f"Season: {settings.season.name} — {describe_season(settings.season)}."


def describe_study_area(settings: WorkspaceSettings) -> str:
    if settings.study_area is None:
        return "Area: none set; a run covers all of its data unless it names an area."
    return f"Area: {area_label(settings.study_area)}."


def describe_settings(settings: WorkspaceSettings) -> str:
    return f"{describe_set_season(settings)} {describe_study_area(settings)}"


def prompt_block(settings: WorkspaceSettings) -> str:
    """The settings as the assistant reads them at the start of each turn."""
    return (
        "\n\nWorkspace settings (defaults for every run here; a value the user gives "
        "in a request overrides them):\n"
        f"- {describe_set_season(settings)}\n- {describe_study_area(settings)}\n"
        "Call set_workspace_season when the user names the season they work in "
        "or changes its dates, and set_workspace_area when they name the place they work in."
    )


# --- imperative shell ---------------------------------------------------------


async def _stored(conn, workspace_id: str) -> str | None:
    result = await conn.execute(
        sa.select(tables.workspaces.c.settings_json).where(tables.workspaces.c.id == workspace_id)
    )
    return result.scalar_one_or_none()


async def get_settings(workspace_id: str) -> WorkspaceSettings:
    async with get_db() as conn:
        return parse_settings(await _stored(conn, workspace_id))


async def update_settings(workspace_id: str, patch: WorkspaceSettings) -> WorkspaceSettings:
    """Apply ``patch`` to a workspace whose caller has been checked to own it."""
    async with get_db() as conn:
        updated = apply_patch(parse_settings(await _stored(conn, workspace_id)), patch)
        await conn.execute(
            tables.workspaces.update()
            .where(tables.workspaces.c.id == workspace_id)
            .values(settings_json=serialize(updated), updated_at=datetime.now(UTC).isoformat())
        )
    return updated
