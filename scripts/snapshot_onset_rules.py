"""Write ROMP's onset-rule schemas to the packaged catalog the server validates against.

The server does not import momp, so rerun this after bumping the momp pin:

    pixi run --manifest-path src/ai_almanac/envs/benchmark.pixi.toml \
        python scripts/snapshot_onset_rules.py

It also records each rule's lookahead, as ROMP computes it, for sample params.
tests/test_onset_rule_lookahead.py checks the server's copy of those formulas
against them, so a ROMP change to a rule's lookahead fails a test here.
"""

import json
from dataclasses import fields
from pathlib import Path

from momp.stats.onset_rule import list_rules

ROOT = Path(__file__).parents[1]
CATALOG = ROOT / "src/ai_almanac/server/config/onset_rules.json"
LOOKAHEAD_SAMPLES = ROOT / "testdata/onset_rule_lookaheads.json"


def sample_params(cls) -> list[dict]:
    """Each rule's defaults, then each integer param at its bounds and a mid value."""
    schema = {spec["name"]: spec for spec in cls.schema()["params"]}
    samples = [{}]
    for field in fields(cls):
        spec = schema.get(field.name, {})
        if not spec.get("type", "").startswith("int"):
            continue
        low, high = spec.get("min", 0), spec.get("max", 30)
        samples.extend({field.name: value} for value in sorted({low, (low + high) // 2, high}))
    return samples


def lookahead_samples(cls) -> list[dict]:
    recorded = []
    for params in sample_params(cls):
        try:
            rule = cls(**params)  # construction runs the rule's own validation
        except ValueError:
            continue
        full_params = {field.name: getattr(rule, field.name) for field in fields(rule)}
        recorded.append({"params": full_params, "lookahead": rule.lookahead})
    return recorded


rules = sorted(list_rules().items())
CATALOG.write_text(json.dumps([cls.schema() for _, cls in rules], indent=2) + "\n")
LOOKAHEAD_SAMPLES.write_text(
    json.dumps({name: lookahead_samples(cls) for name, cls in rules}, indent=2) + "\n"
)
print(f"wrote {len(rules)} onset rules to {CATALOG} and their lookaheads to {LOOKAHEAD_SAMPLES}")
