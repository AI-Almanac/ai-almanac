"""Uniform dataset layout: one tree, mirrored across local / GCS / Modal volume.

The directory layout *is* the catalog. Walking ``{root}`` reconstructs what data
exists on any backend, so the application never tracks a per-system directory
structure — it points each backend at its own ``{root}`` and walks the same
tree. A per-dataset ``manifest.json`` carries the semantics a path can't (the
variable, spatial bounds, ensemble lead structure), so building the catalog
never has to open a NetCDF.

Layout (identical under every backend's root)::

    {root}/obs/{region}/{dataset_id}/{year}.nc
    {root}/forecasts/{region}/{model_id}/{year}.nc
    {root}/{kind}/{region}/{id}/manifest.json

``{root}`` is the only per-backend difference (a local dir, a ``gs://`` prefix,
or a mounted Modal volume path); everything below it is byte-identical, so
mirroring is a dumb tree copy and a freshly-populated volume is immediately
discoverable with no database seeding.

This module is the pure core: parsing a flat listing into a catalog and parsing
a manifest blob into a model. Reading bytes from a backend lives in the storage
service; the walker here takes the listing a backend already produces.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ValidationError

# Bump when the manifest's required shape changes so old blobs can be migrated
# instead of silently misparsed.
SCHEMA_VERSION = 1

OBS = "obs"
FORECASTS = "forecasts"
KINDS = frozenset({OBS, FORECASTS})

MANIFEST_NAME = "manifest.json"

# Filenames are strictly four-digit years so stray files can't pollute the
# catalog. The uniform layout normalizes every source to ``{year}.nc`` (no
# ``data_{}.nc`` / pattern variations).
_YEAR_FILE = re.compile(r"^(\d{4})\.nc$")


@dataclass(frozen=True, order=True)
class DatasetRef:
    """Identity of one dataset: the path discriminators the platform filters on."""

    kind: str
    region: str
    id: str

    @property
    def prefix(self) -> str:
        return f"{self.kind}/{self.region}/{self.id}"

    @property
    def manifest_key(self) -> str:
        return f"{self.prefix}/{MANIFEST_NAME}"

    def year_key(self, year: int) -> str:
        return f"{self.prefix}/{year}.nc"


class Manifest(BaseModel):
    """Self-describing metadata co-located with a dataset's files.

    Holds exactly what a path can't and what listing shouldn't open files to
    learn. Parsed at the trust boundary (``parse``); once built it can be
    trusted. Fields beyond ``kind``/``region``/``id``/``var`` are optional so a
    minimal manifest is still valid and the model can grow without a flag day.
    """

    schema_version: int = SCHEMA_VERSION
    kind: str
    region: str
    id: str
    var: str
    unit_cvt: float = 1.0
    spatial_bounds: dict[str, float] | None = None
    init_days: str | None = None
    probabilistic: bool = False
    members: int | None = None
    model_type: str | None = None
    start_year_clim: int | None = None
    end_year_clim: int | None = None
    # Forecast lead structure: True when files carry the ensemble ``number`` dim
    # and daily-lead ``day`` dim the blend/benchmark readers expect. Lets
    # compatibility checks run without opening a file.
    ensemble: bool = False

    @property
    def ref(self) -> DatasetRef:
        return DatasetRef(self.kind, self.region, self.id)

    @classmethod
    def parse(cls, blob: str | bytes) -> Manifest:
        return cls.model_validate_json(blob)

    def dumps(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=True)


@dataclass(frozen=True)
class DatasetEntry:
    """A discovered dataset: its identity and the years available on disk."""

    ref: DatasetRef
    years: tuple[int, ...]


def parse_year_file(relpath: str) -> tuple[DatasetRef, int] | None:
    """Parse ``{kind}/{region}/{id}/{year}.nc`` → (ref, year), else None.

    None means "not a data file under the convention" — a manifest, a stray
    file, or a wrong-depth path. Callers skip those rather than failing the walk.
    """
    parts = PurePosixPath(relpath).parts
    if len(parts) != 4:
        return None
    kind, region, dataset_id, filename = parts
    if kind not in KINDS:
        return None
    match = _YEAR_FILE.match(filename)
    if not match:
        return None
    return DatasetRef(kind, region, dataset_id), int(match.group(1))


def build_catalog(relpaths: Iterable[str]) -> list[DatasetEntry]:
    """Group a flat listing (paths relative to ``{root}``) into datasets.

    Pure: takes whatever a backend's directory/blob listing yields and returns
    the catalog, sorted by ``prefix`` with each dataset's years ascending.
    Non-conforming paths (manifests, junk) are ignored.
    """
    years: dict[DatasetRef, set[int]] = {}
    for relpath in relpaths:
        parsed = parse_year_file(relpath)
        if parsed is None:
            continue
        ref, year = parsed
        years.setdefault(ref, set()).add(year)
    return [
        DatasetEntry(ref=ref, years=tuple(sorted(years[ref])))
        for ref in sorted(years)
    ]


@dataclass(frozen=True)
class Dataset:
    """A discovered dataset: its identity, available years, and parsed manifest.

    ``manifest`` is None when a dataset has data files but no (or an unparseable)
    ``manifest.json``. The dataset still lists — discovery degrades to years-only
    rather than dropping data the user can see on disk.
    """

    ref: DatasetRef
    years: tuple[int, ...]
    manifest: Manifest | None


def discover(
    list_paths: Callable[[], Iterable[str]],
    read_manifest: Callable[[DatasetRef], str | bytes | None],
) -> list[Dataset]:
    """Walk a backend into a manifest-enriched catalog.

    I/O is injected so the same composition serves every backend (local, GCS,
    Modal volume) and is testable with in-memory fakes: ``list_paths`` yields
    relpaths under ``{root}``; ``read_manifest`` returns a dataset's manifest
    blob or None.
    """
    datasets: list[Dataset] = []
    for entry in build_catalog(list_paths()):
        datasets.append(
            Dataset(entry.ref, entry.years, _read_manifest(read_manifest, entry.ref))
        )
    return datasets


def _read_manifest(
    read_manifest: Callable[[DatasetRef], str | bytes | None], ref: DatasetRef
) -> Manifest | None:
    blob = read_manifest(ref)
    if not blob:
        return None
    try:
        return Manifest.parse(blob)
    except ValidationError:
        # ponytail: a malformed manifest degrades that dataset to years-only.
        # Surface per-dataset manifest errors to the caller if operators need to
        # know which file is broken.
        return None


def discover_datasets() -> list[Dataset]:
    """Discover datasets on the active storage backend (local / GCS / volume).

    Thin adapter wiring ``get_storage()`` into the pure ``discover``; imported
    lazily so this module's core stays free of the storage dependency.
    """
    from ai_almanac.server.services.storage import get_storage

    storage = get_storage()
    return discover(
        storage.list_dataset_tree,
        lambda ref: storage.read_dataset_text(ref.manifest_key),
    )


def resolve_dataset_uri(ref: DatasetRef) -> str:
    """Concrete URI of a dataset dir on the active backend (path or ``gs://``)."""
    from ai_almanac.server.services.storage import get_storage

    return get_storage().dataset_uri(ref.prefix)


def staging_uris(ref: DatasetRef, years: Iterable[int]) -> list[str]:
    """Per-year file URIs to stage for a dataset — the year-filtered set only.

    The unit of staging is one ``{year}.nc`` file, so a job pulls exactly the
    years it uses instead of a whole (potentially GB-scale) dataset dir.
    """
    base = resolve_dataset_uri(ref).rstrip("/")
    return [f"{base}/{year}.nc" for year in years]
