"""Uniform dataset layout: path parsing, catalog walk, manifest round-trip."""

from __future__ import annotations

import pytest

from ai_almanac.server.services.data_catalog import (
    FORECASTS,
    OBS,
    DatasetRef,
    Manifest,
    build_catalog,
    parse_year_file,
)


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
