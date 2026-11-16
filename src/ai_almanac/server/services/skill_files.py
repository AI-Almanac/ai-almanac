"""The names a benchmark gives its skill grid files.

ROMP writes ``spatial_metrics_<model>_<window>.nc`` per model and lead window, with the
window as ``1-15`` or, in older runs, ``1,15``; Earth2Studio scoring writes
``e2s_spatial_metrics_<model>_all.nc``. Every reader parses and builds the names here.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

ROMP_PREFIX = "spatial_metrics"
E2S_PREFIX = "e2s_spatial_metrics"
PREFIXES = (ROMP_PREFIX, E2S_PREFIX)
_SUFFIX = ".nc"
_STEM_RE = re.compile(r"^(?P<e2s>e2s_)?spatial_metrics_(?P<model>.+)_(?P<window>\d+[-,]\d+|all)$")


@dataclass(frozen=True)
class SkillFileName:
    """Which model and lead window a skill grid file scores, and who wrote it."""

    model: str
    # "1-15" whichever separator the file used; "all" for every lead day.
    window: str
    e2s: bool = False

    @classmethod
    def parse(cls, name: str) -> SkillFileName | None:
        """A filename or stem as a skill file's name; None for any other file."""
        path = PurePosixPath(name)
        stem = path.stem if path.suffix == _SUFFIX else name
        match = _STEM_RE.match(stem)
        if match is None:
            return None
        return cls(match["model"], match["window"].replace(",", "-"), bool(match["e2s"]))


def is_skill_file(filename: str) -> bool:
    return filename.endswith(_SUFFIX) and SkillFileName.parse(filename) is not None


def glob_patterns() -> tuple[str, ...]:
    """Patterns matching every skill file in a job's output directory, ROMP's first."""
    return tuple(f"{prefix}_*{_SUFFIX}" for prefix in PREFIXES)


def candidate_names(model: str, window: str) -> tuple[str, ...]:
    """The filenames a model's grid for ``window`` may have been written under, in the
    order to look for them."""
    return tuple(
        f"{prefix}_{model}_{written}{_SUFFIX}"
        for prefix in PREFIXES
        for written in dict.fromkeys((window, window.replace("-", ",")))
    )


def window_start(window: str) -> float:
    """The first lead day of a window, for ordering; a window without one sorts last."""
    start = window.split("-", 1)[0]
    return int(start) if start.isdigit() else math.inf
