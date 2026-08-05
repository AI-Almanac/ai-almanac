"""Assistant rulesets — the assistant's policy as data rather than as code.

A ruleset is one named, versioned answer to "how should the assistant behave":
an ordered list of prompt sections, a tool policy, and optionally a model and
sampling settings. Packaged YAML in ``config/rulesets/`` supplies the defaults so
they are reviewable in a diff; those rows are seeded into
``assistant_rulesets``, where an admin can clone and edit them at runtime.

Guardrail *thresholds* are not part of a ruleset. They decide what the platform
accepts, so they live in the settings overlay and are read through
``guardrails.current()`` by the submission chokepoint, the validation display,
and the prose rendering alike — one number, so the wording an admin reads is the
number that will be enforced.

What a ruleset does *not* do is enforce anything. The statistical rules hold
because ``services.guardrails`` checks them at the submission chokepoint, past
the model. A ruleset decides how well the assistant *explains* those rules —
which is exactly why it is safe to make it editable at runtime, and why the
``unconstrained`` control arm can strip the prose without weakening the
platform.

Section bodies may reference guardrail thresholds as ``{{min_training_years}}``.
Double braces are deliberate: the prompt contains a Python code example with
single braces, so ``str.format`` is not usable here.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Literal

import sqlalchemy as sa
import yaml
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from ai_almanac.server.services import guardrails as guardrails_module
from ai_almanac.server.services.guardrails import Guardrails

logger = logging.getLogger(__name__)

# Resolved through importlib.resources so the packaged YAML is found in an
# installed wheel as well as in the source tree, matching settings.py.
_RULESETS_DIR = Path(str(files("ai_almanac.server").joinpath("config", "rulesets")))

BUILTIN_RULESET_ID = "builtin"

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PromptSection(BaseModel):
    """One toggleable block of the system prompt."""

    key: str
    title: str = ""
    body: str
    # A required section cannot be disabled or removed by an edit. This guards
    # an admin retouching a ruleset from silently dropping the statistical
    # cautions — the failure mode the old wholesale `chat_system_prompt`
    # override had. It is not a claim that no ruleset may omit the section: the
    # `unconstrained` control arm deliberately has no caveats section at all,
    # which is the point of having it.
    required: bool = False
    enabled: bool = True
    # Scope kinds this section applies to; empty means every scope.
    scope_kinds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _required_sections_stay_enabled(self) -> PromptSection:
        if self.required and not self.enabled:
            raise ValueError(f"prompt section {self.key!r} is required and cannot be disabled")
        return self


class ToolPolicy(BaseModel):
    """Tools withheld from the assistant.

    Applied at registration, so a denied tool has no schema entry and there is
    nothing for the model to attempt.
    """

    deny: list[str] = Field(default_factory=list)


class Ruleset(BaseModel):
    id: str
    name: str
    description: str = ""
    version: int = 1
    source: Literal["packaged", "custom"] = "custom"
    prompt_sections: list[PromptSection] = Field(default_factory=list)
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    # None means "use the model the user's profile resolves to".
    model: str | None = None
    model_settings: dict | None = None


def render_section(body: str, guardrails: Guardrails) -> str:
    """Substitute ``{{threshold}}`` references, leaving unknown names in place.

    Leaving an unknown placeholder visible is deliberate: a typo shows up in the
    prompt where an admin previewing the ruleset will see it, rather than
    raising at request time and taking chat down.
    """
    values = {field: getattr(guardrails, field) for field in guardrails.__dataclass_fields__}

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _PLACEHOLDER.sub(substitute, body)


def sections_for_scope(ruleset: Ruleset, scope_kind: str) -> list[PromptSection]:
    return [
        section
        for section in ruleset.prompt_sections
        if (section.enabled or section.required)
        and (not section.scope_kinds or scope_kind in section.scope_kinds)
    ]


def build_instructions(
    ruleset: Ruleset, scope_kind: str, guardrails: Guardrails | None = None
) -> str:
    """The system prompt for one ruleset and scope kind.

    Thresholds default to the platform's live values, so the prose an admin
    reads is the number the chokepoint will enforce. Scope *values* (ids, keys)
    are appended by the caller in ``llm.py``, which owns the token sanitizing —
    that guard is tested and stays where it is.
    """
    guardrails = guardrails or guardrails_module.current()
    bodies = [
        render_section(section.body, guardrails).strip()
        for section in sections_for_scope(ruleset, scope_kind)
    ]
    return "\n\n".join(body for body in bodies if body)


# ---------------------------------------------------------------------------
# Packaged rulesets
# ---------------------------------------------------------------------------


def packaged_rulesets() -> list[Ruleset]:
    """Rulesets shipped with the package, ordered by filename."""
    rulesets = []
    for path in sorted(_RULESETS_DIR.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text())
        payload["source"] = "packaged"
        payload.setdefault("id", path.stem)
        rulesets.append(Ruleset.model_validate(payload))
    return rulesets


def packaged_ruleset(ruleset_id: str) -> Ruleset:
    for ruleset in packaged_rulesets():
        if ruleset.id == ruleset_id:
            return ruleset
    raise KeyError(f"no packaged ruleset {ruleset_id!r}")


# ---------------------------------------------------------------------------
# Stored rulesets
# ---------------------------------------------------------------------------

IMPORTED_OVERRIDE_ID = "imported-override"

_COLUMNS = (
    "id, name, description, version, source, is_active, archived, comparison_enabled, "
    "prompt_sections, tool_policy, model, model_settings"
)


def _row_to_ruleset(row) -> Ruleset:
    def parsed(value, fallback):
        if value is None:
            return fallback
        return json.loads(value) if isinstance(value, str) else value

    return Ruleset(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        version=row["version"],
        source=row["source"],
        prompt_sections=parsed(row["prompt_sections"], []),
        tool_policy=parsed(row["tool_policy"], {}),
        model=row["model"],
        model_settings=parsed(row["model_settings"], None),
    )


async def _upsert(conn, ruleset: Ruleset, *, is_active: bool, created_by: str | None) -> None:
    now = datetime.now(UTC).isoformat()
    payload = {
        "id": ruleset.id,
        "name": ruleset.name,
        "description": ruleset.description,
        "version": ruleset.version,
        "source": ruleset.source,
        "is_active": is_active,
        "prompt_sections": json.dumps(
            [section.model_dump() for section in ruleset.prompt_sections]
        ),
        "tool_policy": json.dumps(ruleset.tool_policy.model_dump()),
        "model": ruleset.model,
        "model_settings": json.dumps(ruleset.model_settings) if ruleset.model_settings else None,
        "created_by": created_by,
        "now": now,
    }
    existing = (
        await conn.execute(
            sa.text("SELECT id FROM assistant_rulesets WHERE id = :id"), {"id": ruleset.id}
        )
    ).fetchone()
    if existing:
        await conn.execute(
            sa.text(
                "UPDATE assistant_rulesets SET name = :name, description = :description, "
                "version = :version, source = :source, prompt_sections = :prompt_sections, "
                "tool_policy = :tool_policy, model = :model, "
                "model_settings = :model_settings, archived = FALSE, updated_at = :now "
                "WHERE id = :id"
            ),
            {k: v for k, v in payload.items() if k not in ("is_active", "created_by")},
        )
        return
    await conn.execute(
        sa.text(
            "INSERT INTO assistant_rulesets "
            "(id, name, description, version, source, is_active, archived, prompt_sections, "
            " tool_policy, model, model_settings, created_by, created_at, updated_at) "
            "VALUES (:id, :name, :description, :version, :source, :is_active, FALSE, "
            " :prompt_sections, :tool_policy, :model, :model_settings, "
            " :created_by, :now, :now)"
        ),
        payload,
    )


async def seed_packaged_rulesets() -> None:
    """Refresh the packaged rows and make sure exactly one ruleset is active.

    Packaged rows are overwritten from YAML on every startup, so editing the
    file is how their defaults change; ``source = 'custom'`` rows are never
    touched. A deployment that had set ``chat_system_prompt`` is carried over as
    a custom ruleset so its wording is not silently dropped — the settings field
    itself is deprecated but still read once, here.
    """
    from ai_almanac.server.db import get_db
    from ai_almanac.settings import settings

    async with get_db() as conn:
        for ruleset in packaged_rulesets():
            await _upsert(conn, ruleset, is_active=False, created_by=None)

        override = settings.chat_system_prompt.strip()
        if override:
            imported = Ruleset(
                id=IMPORTED_OVERRIDE_ID,
                name="Imported override",
                description=(
                    "Carried over from the deprecated chat_system_prompt setting. "
                    "The statistical caveats are re-attached as a required section."
                ),
                source="custom",
                prompt_sections=[
                    PromptSection(key="imported", title="Imported prompt", body=override),
                    next(
                        section
                        for section in packaged_ruleset(BUILTIN_RULESET_ID).prompt_sections
                        if section.key == "caveats"
                    ),
                ],
            )
            existing = (
                await conn.execute(
                    sa.text("SELECT id FROM assistant_rulesets WHERE id = :id"),
                    {"id": IMPORTED_OVERRIDE_ID},
                )
            ).fetchone()
            if not existing:
                await _upsert(conn, imported, is_active=False, created_by=None)

        active = (
            await conn.execute(sa.text("SELECT id FROM assistant_rulesets WHERE is_active = TRUE"))
        ).fetchone()
        if not active:
            default_id = IMPORTED_OVERRIDE_ID if override else BUILTIN_RULESET_ID
            await conn.execute(
                sa.text("UPDATE assistant_rulesets SET is_active = TRUE WHERE id = :id"),
                {"id": default_id},
            )


@dataclass(frozen=True)
class StoredRuleset:
    """A ruleset row with its deployment state, which is not ruleset content."""

    ruleset: Ruleset
    is_active: bool
    comparison_enabled: bool


async def list_rulesets(*, include_archived: bool = False) -> list[StoredRuleset]:
    """Every stored ruleset with its deployment state."""
    from ai_almanac.server.db import get_db

    query = f"SELECT {_COLUMNS} FROM assistant_rulesets"
    if not include_archived:
        query += " WHERE archived = FALSE"
    query += " ORDER BY source, id"
    async with get_db() as conn:
        rows = (await conn.execute(sa.text(query))).mappings().fetchall()
    return [
        StoredRuleset(
            ruleset=_row_to_ruleset(row),
            is_active=bool(row["is_active"]),
            comparison_enabled=bool(row["comparison_enabled"]),
        )
        for row in rows
    ]


async def set_comparison_enabled(ruleset_id: str, enabled: bool) -> None:
    """Expose or hide a ruleset for users. ``KeyError`` when there is no row."""
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        result = await conn.execute(
            sa.text(
                "UPDATE assistant_rulesets SET comparison_enabled = :enabled "
                "WHERE id = :id AND archived = FALSE"
            ),
            {"id": ruleset_id, "enabled": enabled},
        )
    if result.rowcount == 0:
        raise KeyError(ruleset_id)


async def archive_ruleset(ruleset_id: str) -> None:
    """Remove a custom ruleset from every list, keeping the row.

    Archive rather than hard delete: turn logs reference ruleset ids, and the
    feedback rollup should keep naming the wording that produced its numbers.
    The caller refuses packaged ids (reseeding would resurrect them) and the
    active ruleset (chat must always resolve one).
    """
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        result = await conn.execute(
            sa.text(
                "UPDATE assistant_rulesets SET archived = TRUE, comparison_enabled = FALSE "
                "WHERE id = :id AND is_active = FALSE AND archived = FALSE"
            ),
            {"id": ruleset_id},
        )
    if result.rowcount == 0:
        raise KeyError(ruleset_id)


async def get_ruleset(ruleset_id: str) -> Ruleset | None:
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    sa.text(f"SELECT {_COLUMNS} FROM assistant_rulesets WHERE id = :id"),
                    {"id": ruleset_id},
                )
            )
            .mappings()
            .fetchone()
        )
    return _row_to_ruleset(row) if row else None


async def selectable_ruleset(ruleset_id: str) -> Ruleset | None:
    """A stored ruleset an admin has exposed to users.

    The user-facing counterpart of ``get_ruleset``, which deliberately returns
    archived and unexposed rows for admin use. Gates session pinning and
    user-chosen comparison arms alike; None means the caller falls back to the
    active ruleset (or refuses, for a comparison arm).
    """
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    sa.text(
                        f"SELECT {_COLUMNS} FROM assistant_rulesets "
                        "WHERE id = :id AND archived = FALSE AND comparison_enabled = TRUE"
                    ),
                    {"id": ruleset_id},
                )
            )
            .mappings()
            .fetchone()
        )
    return _row_to_ruleset(row) if row else None


async def active_ruleset() -> Ruleset:
    """The ruleset in force, falling back to the packaged built-in.

    Never raises. Chat must not go down because the table is empty, a seed has
    not run yet, or a row was archived — and the packaged built-in is the
    behaviour the deployment shipped with, which is the right thing to fall back
    to.
    """
    from ai_almanac.server.db import get_db

    try:
        async with get_db() as conn:
            row = (
                (
                    await conn.execute(
                        sa.text(
                            f"SELECT {_COLUMNS} FROM assistant_rulesets "
                            "WHERE is_active = TRUE AND archived = FALSE"
                        )
                    )
                )
                .mappings()
                .fetchone()
            )
        if row:
            return _row_to_ruleset(row)
    except SQLAlchemyError:
        logger.exception("assistant ruleset lookup failed; using the packaged built-in")
    return packaged_ruleset(BUILTIN_RULESET_ID)


class PackagedRulesetIdError(ValueError):
    """Raised when a save would land on an id that seeding will overwrite."""


async def save_ruleset(ruleset: Ruleset, *, created_by: str | None = None) -> Ruleset:
    """Write a custom ruleset.

    Rejects a save onto a packaged id. ``seed_packaged_rulesets`` rewrites those
    rows from YAML on every startup, so such a save appears to work and is then
    silently reverted on the next restart — the admin loses the edit and has no
    signal that it happened. Cloning to a new id is the supported path, and it is
    what the caller must do instead.
    """
    from ai_almanac.server.db import get_db

    if any(ruleset.id == packaged.id for packaged in packaged_rulesets()):
        raise PackagedRulesetIdError(
            f"{ruleset.id!r} is a packaged ruleset and is rewritten from its YAML on "
            "every startup. Clone it to a new id to keep your changes."
        )

    stored = (
        ruleset if ruleset.source == "custom" else ruleset.model_copy(update={"source": "custom"})
    )
    async with get_db() as conn:
        await _upsert(conn, stored, is_active=False, created_by=created_by)
    return stored


async def activate_ruleset(ruleset_id: str) -> None:
    """Make one ruleset active. The partial unique index enforces at most one,
    so the clear has to land before the set."""
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        await conn.execute(
            sa.text("UPDATE assistant_rulesets SET is_active = FALSE WHERE is_active = TRUE")
        )
        result = await conn.execute(
            sa.text(
                "UPDATE assistant_rulesets SET is_active = TRUE WHERE id = :id AND archived = FALSE"
            ),
            {"id": ruleset_id},
        )
        if result.rowcount == 0:
            raise KeyError(f"no ruleset {ruleset_id!r} to activate")


def next_version(ruleset: Ruleset, new_id: str, name: str) -> Ruleset:
    """A custom copy of a ruleset, one version up.

    Editing in place would lose the wording that produced the transcripts
    already logged against the old version, which is the whole point of
    recording a version on a turn.
    """
    return ruleset.model_copy(
        update={
            "id": new_id,
            "name": name,
            "version": ruleset.version + 1,
            "source": "custom",
        }
    )
