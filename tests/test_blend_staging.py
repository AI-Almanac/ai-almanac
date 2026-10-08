"""Blend runs stage per-year files from GCS, then process only the files the
intermediates cache doesn't already hold."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_APP_PATH = Path(__file__).parents[1] / "modal" / "blending_app.py"


def _load_blending_app():
    spec = importlib.util.spec_from_file_location("almanac_blending_app", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Blob:
    def __init__(self, store: dict[str, bytes], bucket: str, name: str) -> None:
        self._store, self._key, self.name = store, f"{bucket}/{name}", name

    def exists(self) -> bool:
        return self._key in self._store

    def download_to_filename(self, path: str) -> None:
        Path(path).write_bytes(self._store[self._key])


class _Client:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def bucket(self, bucket: str):
        store = self._store

        class _Bucket:
            def blob(self, name: str) -> _Blob:
                return _Blob(store, bucket, name)

        return _Bucket()


def test_staging_downloads_every_year_file_under_its_own_name(tmp_path: Path) -> None:
    app = _load_blending_app()
    store = {f"data/models/india/aifs/{year}.nc": str(year).encode() for year in range(1990, 2010)}
    uris = [f"gs://{key}" for key in store]

    count = app._stage_uris(_Client(store), uris, tmp_path, "forecast aifs")

    assert count == len(uris)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == {
        Path(key).name: content for key, content in store.items()
    }


def test_staging_fails_loudly_when_a_year_file_is_missing(tmp_path: Path) -> None:
    app = _load_blending_app()
    store = {"data/aifs/2000.nc": b"2000"}
    uris = ["gs://data/aifs/2000.nc", "gs://data/aifs/2001.nc"]

    with pytest.raises(FileNotFoundError, match="2001.nc"):
        app._stage_uris(_Client(store), uris, tmp_path, "forecast aifs")


def test_cached_parts_only_compute_cache_misses_and_keep_file_order(tmp_path: Path) -> None:
    app = _load_blending_app()
    cache_dir = str(tmp_path / "cache")
    paths = [tmp_path / f"{year}.nc" for year in (2001, 2002, 2003)]
    keys = [{"file": path.name} for path in paths]
    app._cached_pickle(cache_dir, "obs", keys[1], lambda: "cached 2002")
    computed: list[str] = []

    def process(path: Path, context: dict) -> str:
        computed.append(path.name)
        return f"{context['label']} {path.stem}"

    results = app._cached_parts(cache_dir, "obs", keys, paths, process, {"label": "fresh"}, 1)

    assert results == [("fresh 2001", False), ("cached 2002", True), ("fresh 2003", False)]
    assert computed == ["2001.nc", "2003.nc"]
    rerun = app._cached_parts(cache_dir, "obs", keys, paths, process, {"label": "fresh"}, 1)
    assert all(was_cached for _, was_cached in rerun)
    assert computed == ["2001.nc", "2003.nc"]


def test_parts_run_serially_with_a_log_line_when_workers_cannot_import_the_module(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _load_blending_app()  # synthetic module name, as the local runner loads it
    paths = [tmp_path / "b.nc", tmp_path / "a.nc"]

    result = app._map_parts(lambda path, ctx: (path.name, ctx), paths, "ctx", workers=4)

    assert result == [("b.nc", "ctx"), ("a.nc", "ctx")]
    assert "running 2 files serially" in capsys.readouterr().out


def test_a_failing_worker_fails_the_whole_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Imported by its real name, as in the Modal container, so spawned workers
    # can re-import it and the process pool is actually used.
    monkeypatch.syspath_prepend(str(_APP_PATH.parent))
    import blending_app

    missing = [tmp_path / "missing-1.nc", tmp_path / "missing-2.nc"]
    with pytest.raises(Exception):  # noqa: B017 - any worker error must surface
        blending_app._map_parts(blending_app._process_obs_part, missing, {}, workers=2)


def test_registered_dims_are_renamed_to_the_names_blending_reads() -> None:
    app = _load_blending_app()
    base = {"input": {}, "dimensions": {"rename": {"day": "day", "number": "number"}}}
    dims = {
        "init_time": "time",
        "step": "prediction_timedelta_daily",
        "member": "realization",
        "lat": "latitude",
        "lon": "longitude",
    }

    renames = app._forecast_spec_for(base, dims)["dimensions"]["rename"]

    assert renames["prediction_timedelta_daily"] == "day"
    assert renames["realization"] == "number"
    assert renames["latitude"] == "lat"
    assert renames["day"] == "day"  # the shared renames still apply


def test_sources_without_registered_dims_keep_the_shared_spec() -> None:
    app = _load_blending_app()
    base = {"input": {}, "dimensions": {"rename": {"day": "day"}}}

    assert app._forecast_spec_for(base, None) is base


def test_forecast_rainfall_is_scaled_to_millimetres() -> None:
    import pandas as pd

    app = _load_blending_app()
    df = pd.DataFrame({"lat": [10.0], "rain_day_1": [0.004], "rain_day_2": [0.02]})

    scaled = app._in_millimetres(df, 1000.0)

    assert scaled["rain_day_1"].tolist() == pytest.approx([4.0])
    assert scaled["rain_day_2"].tolist() == pytest.approx([20.0])
    assert scaled["lat"].tolist() == [10.0]


def test_only_cells_with_enough_observed_onset_years_are_blendable() -> None:
    import pandas as pd

    app = _load_blending_app()
    obs_wide = pd.DataFrame(
        {
            "id": ["land", "land", "land", "edge", "edge", "edge", "sea", "sea"],
            "year": [2010, 2011, 2012, 2010, 2011, 2012, 2010, 2011],
            "onset_day": [150, 160, 155, 150, None, 158, None, None],
        }
    )

    assert app._blendable_ids(obs_wide, min_onset_years=3) == {"land"}
    assert app._blendable_ids(obs_wide, min_onset_years=2) == {"land", "edge"}


def test_forecast_rows_outside_the_blendable_cells_are_dropped() -> None:
    import pandas as pd

    app = _load_blending_app()
    df = pd.DataFrame({"id": ["land", "sea", "land"], "rain_day_1": [1.0, 2.0, 3.0]})

    assert app._only_cells(df, frozenset({"land"}))["rain_day_1"].tolist() == [1.0, 3.0]
    assert app._only_cells(df, None) is df


def test_year_slices_keep_their_rows_positions_in_the_full_table(tmp_path: Path) -> None:
    import pandas as pd

    app = _load_blending_app()
    clim = pd.DataFrame({"id": ["b", "a", "b", "a"], "year": [2012, 2012, 2013, 2013]})

    paths = app._write_year_slices(clim, tmp_path, "conditional", [[2012], [2013]])

    assert pd.read_pickle(paths[1]).index.tolist() == [2, 3]


def test_year_by_year_partitions_reassemble_in_climatology_row_order(tmp_path: Path) -> None:
    import pandas as pd

    app = _load_blending_app()
    row = app._CLIM_ROW
    # Climatology order is cell b before cell a; each year's partition keeps it.
    first = pd.DataFrame({"id": ["b", "a"], "year": [2012, 2012], row: [0, 2]})
    second = pd.DataFrame({"id": ["b", "a"], "year": [2013, 2013], row: [1, 3]})
    paths = [tmp_path / "0.pkl", tmp_path / "1.pkl"]
    first.to_pickle(paths[0])
    second.to_pickle(paths[1])

    combined = app._assemble_combined(paths, restore_clim_order=True)

    assert combined[["id", "year"]].values.tolist() == [
        ["b", 2012],
        ["b", 2013],
        ["a", 2012],
        ["a", 2013],
    ]
    assert row not in combined.columns
    assert combined.index.tolist() == [0, 1, 2, 3]


def _install_order_keeping_combine_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand-ins for onset_blending's combine readers (not installed in CI).

    Like the real read_and_format_climatology_wide / format_forecast_family,
    they keep each table's rows in file order and the climatology's index, which
    is what the year-by-year combine relies on to restore the one-shot order.
    """
    import sys
    import types

    import pandas as pd

    def read_and_format_climatology_wide(path, out_prefix="clim_p_onset"):
        table = pd.read_pickle(path)
        renames = {"predicted_prob_day_1": f"{out_prefix}_day_1"}
        return table[["id", "time", "year", "predicted_prob_day_1"]].rename(columns=renames)

    def format_forecast_family(name, conf):
        start, _, end = conf["sources"][0]["years"].partition(":")
        table = pd.concat([pd.read_pickle(s["file"]) for s in conf["sources"]], ignore_index=True)
        table = table[table["year"].between(int(start), int(end))]
        keys = ["id", "time", "year"]
        return {
            "daily": table[[*keys, "predicted_prob_day_1"]].rename(
                columns={"predicted_prob_day_1": f"{name}_p_onset_day_1"}
            ),
            "constants": table[[*keys, "onset_thresh"]].rename(
                columns={"onset_thresh": f"{name}_onset_thresh"}
            ),
        }

    utils = types.ModuleType("python.prepare_data.combine_forecasts_utils")
    utils.read_and_format_climatology_wide = read_and_format_climatology_wide
    utils.format_forecast_family = format_forecast_family
    monkeypatch.setitem(sys.modules, "python", types.ModuleType("python"))
    monkeypatch.setitem(sys.modules, "python.prepare_data", types.ModuleType("python.prepare_data"))
    monkeypatch.setitem(sys.modules, "python.prepare_data.combine_forecasts_utils", utils)


def test_year_by_year_combine_matches_combining_all_years_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    import pandas as pd

    _install_order_keeping_combine_readers(monkeypatch)
    app = _load_blending_app()
    years = [2012, 2013]
    # Climatology order puts cell "c2" before "c1"; forecasts arrive id-sorted.
    keys = [
        (cell, date(year, 5, day), year)
        for cell in ("c2", "c1")
        for year in years
        for day in (1, 5, 9)
    ]
    clim = pd.DataFrame(keys, columns=["id", "time", "year"]).assign(
        predicted_prob_day_1=[n / 100 for n in range(len(keys))]
    )
    truth = pd.DataFrame(
        {
            "id": ["c1", "c1", "c2", "c2"],
            "year": [2012, 2013, 2012, 2013],
            "true_onset_day": [5, None, 9, 1],
            # Issues after the onset are trimmed: c1's May 9 2012, c2's May 5 and 9 2013.
            "true_onset_date": [date(2012, 5, 5), pd.NaT, date(2012, 5, 9), date(2013, 5, 1)],
        }
    )
    part_paths = {}
    for model in ("aifs", "graphcast"):
        part_paths[model] = []
        for year in years:
            part = (
                clim[clim["year"] == year]
                .sort_values("id")[["id", "time", "year"]]
                .assign(predicted_prob_day_1=0.5, onset_thresh=20.0)
            )
            path = tmp_path / f"{model}_{year}.pkl"
            part.to_pickle(path)
            part_paths[model].append(path)
    confs = {model: app._forecast_family_conf(45, has_spread=False) for model in part_paths}

    def combine(partitions: list[list[int]], name: str) -> list[Path]:
        out = tmp_path / name
        out.mkdir()
        clim_paths = app._write_year_slices(clim, out, "conditional", partitions)
        unc_paths = app._write_year_slices(clim, out, "unconditional", partitions)
        results = []
        for index, part_years in enumerate(partitions):
            model_paths = {
                model: [p for p, year in zip(paths, years, strict=True) if year in part_years]
                for model, paths in part_paths.items()
            }
            frame = app._combine_partition(
                part_years,
                clim_paths[index],
                unc_paths[index],
                model_paths,
                confs,
                truth,
                "inner",
                True,
            )
            result_path = out / f"combined_{index}.pkl"
            frame.to_pickle(result_path)
            results.append(result_path)
        return results

    one_shot = app._assemble_combined(combine([years], "all"), restore_clim_order=False)
    by_year = app._assemble_combined(combine([[2012], [2013]], "yearly"), restore_clim_order=True)

    pd.testing.assert_frame_equal(by_year, one_shot, check_exact=True)
    assert by_year["id"].tolist()[:3] == ["c2", "c2", "c2"]  # climatology order, not id order
    assert len(by_year) == len(keys) - 3  # the post-onset issues were trimmed


def test_ensemble_sized_files_are_processed_fewer_at_a_time() -> None:
    app = _load_blending_app()
    budget = 48 * 2**30
    deterministic = [37_442_250] * 3  # one India 0.25° AIFS v2 year: ~0.7 GB to process
    ensemble = [973_498_500] * 3  # the same grid with 26 members: ~17.5 GB

    assert app._part_workers(deterministic, workers=4, memory_budget_bytes=budget) == 4
    assert app._part_workers(ensemble, workers=4, memory_budget_bytes=budget) == 2
    assert app._part_workers([10**11], workers=4, memory_budget_bytes=budget) == 1
    assert app._part_workers(ensemble, workers=4, memory_budget_bytes=None) == 4


def test_forecast_value_count_reads_only_the_variable_shape(tmp_path: Path) -> None:
    import numpy as np
    import xarray as xr

    app = _load_blending_app()
    path = tmp_path / "2012.nc"
    xr.Dataset({"tp": (("time", "day", "lat", "lon"), np.zeros((2, 3, 4, 5)))}).to_netcdf(path)

    assert app._forecast_value_count(path, "tp") == 120
    assert app._forecast_value_count(path, "missing") == 0
