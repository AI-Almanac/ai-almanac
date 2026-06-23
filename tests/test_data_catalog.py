"""Uniform dataset layout: path parsing, catalog walk, manifest round-trip."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import data_catalog
from ai_almanac.server.services.data_catalog import (
    FORECASTS,
    OBS,
    DatasetRef,
    Manifest,
    build_catalog,
    discover,
    parse_year_file,
    staging_uris,
)
from ai_almanac.server.services.storage import GCSStorage, LocalStorage


@pytest.mark.parametrize(
    "relpath,expected",
    [
        ("forecasts/india/fuxi/2019.nc", (DatasetRef(FORECASTS, "india", "fuxi"), 2019)),
        ("obs/ethiopia/chirps/2001.nc", (DatasetRef(OBS, "ethiopia", "chirps"), 2001)),
    ],
)
def test_parse_year_file_accepts_convention(relpath, expected) -> None:
    assert parse_year_file(relpath) == expected


@pytest.mark.parametrize(
    "relpath",
    [
        "forecasts/india/fuxi/manifest.json",  # manifest, not a year file
        "forecasts/india/fuxi/data_2019.nc",  # legacy pattern is rejected
        "forecasts/india/fuxi/2019.txt",  # wrong extension
        "forecasts/india/2019.nc",  # too shallow (missing id)
        "models/india/fuxi/2019.nc",  # unknown kind
        "forecasts/india/fuxi/extra/2019.nc",  # too deep
    ],
)
def test_parse_year_file_rejects_non_conforming(relpath) -> None:
    assert parse_year_file(relpath) is None


def test_build_catalog_groups_years_and_ignores_junk() -> None:
    listing = [
        "forecasts/india/fuxi/2018.nc",
        "forecasts/india/fuxi/2019.nc",
        "forecasts/india/fuxi/manifest.json",  # ignored
        "forecasts/india/gencast/2020.nc",
        "obs/india/imd/2018.nc",
        "README.md",  # ignored
    ]
    catalog = build_catalog(listing)

    refs = [entry.ref for entry in catalog]
    assert refs == [
        DatasetRef(FORECASTS, "india", "fuxi"),
        DatasetRef(FORECASTS, "india", "gencast"),
        DatasetRef(OBS, "india", "imd"),
    ]
    fuxi = catalog[0]
    assert fuxi.years == (2018, 2019)  # deduped + sorted


def test_manifest_round_trips_through_json() -> None:
    manifest = Manifest(
        kind=FORECASTS,
        region="india",
        id="gencast",
        var="tp",
        unit_cvt=1000.0,
        probabilistic=True,
        ensemble=True,
        spatial_bounds={"lat_min": 5.0, "lat_max": 40.0, "lon_min": 65.0, "lon_max": 100.0},
    )
    reparsed = Manifest.parse(manifest.dumps())
    assert reparsed == manifest
    assert reparsed.ref == DatasetRef(FORECASTS, "india", "gencast")
    assert reparsed.schema_version == 1


def test_dataset_ref_keys_are_layout_paths() -> None:
    ref = DatasetRef(FORECASTS, "india", "fuxi")
    assert ref.year_key(2019) == "forecasts/india/fuxi/2019.nc"
    assert ref.manifest_key == "forecasts/india/fuxi/manifest.json"


# --- discovery (pure, injected I/O) -----------------------------------------

_FUXI = DatasetRef(FORECASTS, "india", "fuxi")
_GENCAST = DatasetRef(FORECASTS, "india", "gencast")


def _manifest_blob(ref: DatasetRef, **over) -> str:
    return Manifest(kind=ref.kind, region=ref.region, id=ref.id, var="tp", **over).dumps()


def test_discover_attaches_manifests_and_tolerates_gaps() -> None:
    listing = [
        "forecasts/india/fuxi/2019.nc",
        "forecasts/india/fuxi/manifest.json",
        "forecasts/india/gencast/2020.nc",  # no manifest
    ]
    manifests = {_FUXI: _manifest_blob(_FUXI, ensemble=True)}

    datasets = discover(lambda: listing, lambda ref: manifests.get(ref))

    by_ref = {d.ref: d for d in datasets}
    assert by_ref[_FUXI].manifest is not None
    assert by_ref[_FUXI].manifest.ensemble is True
    assert by_ref[_GENCAST].years == (2020,)
    assert by_ref[_GENCAST].manifest is None  # missing manifest → years-only


def test_discover_degrades_on_malformed_manifest() -> None:
    listing = ["forecasts/india/fuxi/2019.nc"]
    datasets = discover(lambda: listing, lambda ref: "{not valid json")
    assert datasets[0].manifest is None
    assert datasets[0].years == (2019,)


def test_local_storage_walks_dataset_tree(tmp_path) -> None:
    root = tmp_path / "datasets"
    fuxi = root / "forecasts" / "india" / "fuxi"
    fuxi.mkdir(parents=True)
    (fuxi / "2018.nc").write_bytes(b"")
    (fuxi / "2019.nc").write_bytes(b"")
    (fuxi / "manifest.json").write_text(_manifest_blob(_FUXI, unit_cvt=1000.0))

    storage = LocalStorage(
        upload_dir=tmp_path / "uploads",
        job_outputs_dir=tmp_path / "jobs",
        datasets_dir=root,
    )

    assert storage.list_dataset_tree() == [
        "forecasts/india/fuxi/2018.nc",
        "forecasts/india/fuxi/2019.nc",
        "forecasts/india/fuxi/manifest.json",
    ]

    datasets = discover(
        storage.list_dataset_tree,
        lambda ref: storage.read_dataset_text(ref.manifest_key),
    )
    assert len(datasets) == 1
    assert datasets[0].ref == _FUXI
    assert datasets[0].years == (2018, 2019)
    assert datasets[0].manifest.unit_cvt == 1000.0


def test_local_storage_read_rejects_escaping_key(tmp_path) -> None:
    storage = LocalStorage(
        upload_dir=tmp_path / "uploads",
        job_outputs_dir=tmp_path / "jobs",
        datasets_dir=tmp_path / "datasets",
    )
    assert storage.read_dataset_text("../../etc/passwd") is None


# --- resolver / staging -----------------------------------------------------


def test_local_storage_dataset_uri_joins_under_root(tmp_path) -> None:
    storage = LocalStorage(
        upload_dir=tmp_path / "uploads",
        job_outputs_dir=tmp_path / "jobs",
        datasets_dir=tmp_path / "datasets",
    )
    uri = storage.dataset_uri(_FUXI.prefix)
    assert uri == str((tmp_path / "datasets" / "forecasts" / "india" / "fuxi").resolve())


def test_gcs_storage_dataset_uri_is_bucket_prefix() -> None:
    # A dummy client avoids the google.cloud import; dataset_uri never touches it.
    storage = GCSStorage(
        uploads_bucket="up",
        outputs_bucket="out",
        data_bucket="data",
        client=object(),
    )
    assert storage.dataset_uri(_FUXI.prefix) == "gs://data/datasets/forecasts/india/fuxi"


def test_staging_uris_are_year_filtered(monkeypatch) -> None:
    monkeypatch.setattr(
        data_catalog, "resolve_dataset_uri", lambda ref: "gs://data/datasets/" + ref.prefix
    )
    uris = staging_uris(_FUXI, [2019, 2021])
    assert uris == [
        "gs://data/datasets/forecasts/india/fuxi/2019.nc",
        "gs://data/datasets/forecasts/india/fuxi/2021.nc",
    ]
