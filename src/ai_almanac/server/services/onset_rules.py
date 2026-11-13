"""ROMP's onset definitions, from the catalog written by scripts/snapshot_onset_rules.py.

Checking params here makes a bad definition fail at submission rather than inside a run.
ROMP's cross-field checks (e.g. legacy's dry_extent >= dry_spell) still only run in ROMP.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from importlib.resources import files
from typing import Any

DEFAULT_ONSET_RULE = "legacy"
_ACCEPTED_TYPES = {"int": (int,), "float": (int, float), "bool": (bool,)}
# ROMP's legacy rule takes any of these it is not given from the flat benchmark settings.
_LEGACY_SETTINGS = ("wet_init", "wet_spell", "dry_spell", "dry_extent")


def _packaged_json(filename: str) -> Any:
    return json.loads(files("ai_almanac.server").joinpath("config", filename).read_text())


def _words(identifier: str) -> str:
    words = identifier.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _named(rule: dict, names: Mapping[str, Any]) -> dict:
    """A ROMP rule schema with the words a forecaster reads: a display name, a one-line
    description, and for each parameter a label and a short label (a few words, for a
    run's name). A rule ROMP has added since the names were written falls back to its
    identifier in words, so it still reads sensibly."""
    worded = names.get("params") or {}
    return {
        **rule,
        "display_name": names.get("display_name") or _words(rule["name"]),
        "description": names.get("description"),
        "params": [
            {**spec, **_param_words(spec["name"], worded.get(spec["name"]) or {})}
            for spec in rule["params"]
        ],
    }


def _param_words(name: str, worded: Mapping[str, str]) -> dict[str, str]:
    label = worded.get("label") or _words(name)
    return {"label": label, "short_label": worded.get("short_label") or label.lower()}


@functools.cache
def onset_rules() -> dict[str, dict]:
    """ROMP's rules by identifier. onset_rules.json is rewritten whole from ROMP by
    scripts/snapshot_onset_rules.py, so the names shown to users are kept beside it in
    onset_rule_names.json and joined here."""
    names = _packaged_json("onset_rule_names.json")
    return {
        rule["name"]: _named(rule, names.get(rule["name"]) or {})
        for rule in _packaged_json("onset_rules.json")
    }


def display_name(name: str | None) -> str:
    """What to call an onset definition in front of a user; unset means the default."""
    key = name or DEFAULT_ONSET_RULE
    rule = onset_rules().get(key)
    return rule["display_name"] if rule else _words(key)


def changed_params(
    name: str, rule_params: Mapping[str, Any] | None, settings: Mapping[str, Any]
) -> list[dict]:
    """The rule's parameters whose resolved value differs from ROMP's default, each as
    its label, its short label, value and unit."""
    resolved = resolved_rule_params(name, rule_params, settings)
    return [
        {
            "label": spec["label"],
            "short_label": spec["short_label"],
            "value": resolved[spec["name"]],
            "unit": spec.get("unit"),
        }
        for spec in onset_rules()[name]["params"]
        if resolved[spec["name"]] != spec.get("default")
    ]


def assistant_listing() -> list[dict]:
    """The onset definitions as the assistant is given them: the words to say, then the
    one value a tool call needs, under a name that says it is internal. The rule's own
    identifier is left out of every other field so it has nowhere to be read as a name."""
    return [
        {
            "display_name": rule["display_name"],
            "description": rule["description"],
            "is_default": key == DEFAULT_ONSET_RULE,
            "internal_value": key,
            "params": rule["params"],
        }
        for key, rule in onset_rules().items()
    ]


def onset_rule_errors(name: str, params: dict[str, Any]) -> list[str]:
    rule = onset_rules().get(name)
    if rule is None:
        return [f"Unknown onset rule {name!r}; choose one of {sorted(onset_rules())}"]
    specs = {spec["name"]: spec for spec in rule["params"]}
    unknown = sorted(set(params) - set(specs))
    if unknown:
        return [f"{name}: unknown parameter(s) {unknown}; valid: {sorted(specs)}"]
    return [
        error
        for key, value in params.items()
        if (error := _param_error(f"{name}.{key}", specs[key], value))
    ]


def _param_error(label: str, spec: dict, value: Any) -> str | None:
    type_name, _, optional = spec["type"].partition(" | ")
    if value is None:
        return None if optional == "None" else f"{label} is required"
    if isinstance(value, bool) != (type_name == "bool") or not isinstance(
        value, _ACCEPTED_TYPES[type_name]
    ):
        return f"{label} must be a {type_name}"
    if "choices" in spec and value not in spec["choices"]:
        return f"{label} must be one of {spec['choices']}"
    if "min" in spec and value < spec["min"]:
        return f"{label} must be at least {spec['min']}"
    if "max" in spec and value > spec["max"]:
        return f"{label} must be at most {spec['max']}"
    return None


def resolved_rule_params(
    name: str, rule_params: Mapping[str, Any] | None, settings: Mapping[str, Any]
) -> dict[str, Any]:
    """The params ROMP builds the rule with: its defaults, then for legacy the flat
    benchmark settings, then the params given for the rule."""
    defaults = {spec["name"]: spec.get("default") for spec in onset_rules()[name]["params"]}
    flat = (
        {key: settings[key] for key in _LEGACY_SETTINGS if settings.get(key) is not None}
        if name == "legacy"
        else {}
    )
    return {**defaults, **flat, **(rule_params or {})}


def _legacy_lookahead(params: Mapping[str, Any]) -> int:
    dry_check_on = params["dry_extent"] > params["wet_spell"]
    return (params["dry_extent"] if dry_check_on else params["wet_spell"]) - 1


def _two_stage_lookahead(params: Mapping[str, Any]) -> int:
    return params["stage1_days"] + params["stage2_days"] - 1


def _moron_robertson_lookahead(params: Mapping[str, Any]) -> int:
    veto_days = params["follow_days"] if params["follow_days"] > 0 else 0
    return max(params["wet_days"], veto_days) - 1


# Mirrors each ROMP rule's `lookahead`; tests/test_onset_rule_lookahead.py checks them
# against values ROMP computed, which scripts/snapshot_onset_rules.py records.
_LOOKAHEADS: dict[str, Callable[[Mapping[str, Any]], int]] = {
    "legacy": _legacy_lookahead,
    "two_stage": _two_stage_lookahead,
    "moron_robertson": _moron_robertson_lookahead,
}


def rule_lookahead(name: str, params: Mapping[str, Any]) -> int | None:
    """Days after a candidate onset day the rule reads; None for a rule with no mirror here."""
    lookahead = _LOOKAHEADS.get(name)
    return lookahead(params) if lookahead else None


def onset_fit_errors(
    settings: Mapping[str, Any],
    *,
    forecast_days: int,
    forecast_steps: int | None,
    model_name: str,
) -> list[str]:
    """Why the onset definition in `settings` cannot be scored over `forecast_days`
    on a model whose forecasts hold `forecast_steps` days (None when unknown).

    Expects `settings` whose onset definition already passed `onset_rule_errors`.
    """
    name = settings.get("onset_rule") or DEFAULT_ONSET_RULE
    params = resolved_rule_params(name, settings.get("onset_rule_params"), settings)
    lookahead = rule_lookahead(name, params)
    if lookahead is None:
        return []
    if lookahead >= forecast_days:
        return [
            f"The {display_name(name)} onset definition needs {lookahead} more days after "
            f"a candidate onset day to confirm it, which must be fewer than the "
            f"{forecast_days} forecast days scored. Shorten its windows."
        ]
    needed = forecast_days + lookahead
    if forecast_steps is not None and forecast_steps < needed:
        return [
            f"{model_name} forecasts run {forecast_steps} days, but scoring {forecast_days} "
            f"forecast days with the {display_name(name)} onset definition needs "
            f"{needed}: it reads "
            f"{lookahead} more days after a candidate onset day to confirm it. Shorten "
            f"the definition's windows so it reads at most {forecast_steps - forecast_days} "
            "more days."
        ]
    return []
