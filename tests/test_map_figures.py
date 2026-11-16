"""A map the assistant draws reads well without the model's help: titled and labelled
in words, cropped to the cells that hold data, and coloured on a scale one outlier cell
cannot flatten."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from ai_almanac.server.services import ledger, map_figures, workspaces
from ai_almanac.server.services.focus_area import FocusBox
from ai_almanac.server.services.primitives import code_harness, figures
from tests.helpers.files import write_nc, write_season_nc
from tests.helpers.records import insert_job
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import register_file, workspace_with_session

# --- words -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variable", "label"),
    [
        ("mean_mae", "Onset error (days)"),
        ("false_alarm_rate", "False alarm rate (share of forecast onsets)"),
        ("miss_rate", "Miss rate (share of observed onsets)"),
    ],
)
def test_a_skill_metrics_colour_bar_is_labelled_in_words_with_units(
    variable: str, label: str
) -> None:
    assert map_figures.colour_bar_label(variable) == label
    assert "_" not in map_figures.variable_words(variable)


def test_any_other_variable_is_labelled_by_its_long_name_and_units() -> None:
    rain = map_figures.colour_bar_label("tp", "Total precipitation", "mm")

    assert rain == "Total precipitation (mm)"
    assert map_figures.colour_bar_label("onset_day", None, "1") == "Onset day"
    assert map_figures.colour_bar_label("onset_day") == "Onset day"


def _metrics_file(model: str = "aifs", title: str | None = None) -> dict:
    return {"filename": f"spatial_metrics_{model}_1-15.nc", "item_title": title}


def test_a_panel_is_titled_with_the_files_model_and_lead_days_never_its_file_name() -> None:
    made_by = {"params": {"model_name": "aifs", "model_display_name": "AIFS"}}

    assert map_figures.panel_title(_metrics_file(), made_by) == "AIFS · days 1–15"
    assert map_figures.panel_title(_metrics_file("fuxi"), None) == "fuxi · days 1–15"
    assert (
        map_figures.map_title("Onset error", map_figures.panel_title(_metrics_file(), made_by))
        == "Onset error, AIFS · days 1–15"
    )


def test_a_file_the_user_renamed_is_titled_with_their_name() -> None:
    renamed = _metrics_file(title="AIFS over Amhara, first fortnight")

    assert map_figures.panel_title(renamed, None) == "AIFS over Amhara, first fortnight"


# --- extent ----------------------------------------------------------------------

_LAT = np.arange(3.0, 15.5, 0.5)
_LON = np.arange(33.0, 48.5, 0.5)


def _field_with_data(lat: tuple[float, float], lon: tuple[float, float]) -> np.ndarray:
    """A whole-region grid that is NaN except inside one box, like an area-limited run."""
    inside_lat = (lat[0] <= _LAT) & (lat[1] >= _LAT)
    inside_lon = (lon[0] <= _LON) & (lon[1] >= _LON)
    return np.where(np.outer(inside_lat, inside_lon), 5.0, np.nan)


def test_a_map_is_cropped_to_the_cells_that_hold_data_with_a_margin_of_one_cell() -> None:
    amhara = _field_with_data((9.0, 13.0), (36.0, 40.0))

    extent = map_figures.data_extent(_LAT, _LON, amhara)

    assert extent == map_figures.Extent(8.25, 13.75, 35.25, 40.75)


def test_the_margin_never_reaches_past_the_grids_own_edge() -> None:
    everywhere = np.ones((_LAT.size, _LON.size))

    extent = map_figures.data_extent(_LAT, _LON, everywhere)

    assert extent == map_figures.Extent(2.75, 15.25, 32.75, 48.25)


def test_a_field_with_no_data_has_no_extent_to_crop_to() -> None:
    assert map_figures.data_extent(_LAT, _LON, np.full((_LAT.size, _LON.size), np.nan)) is None


def test_a_grid_stored_north_to_south_crops_to_the_same_box() -> None:
    amhara = _field_with_data((9.0, 13.0), (36.0, 40.0))

    flipped = map_figures.data_extent(_LAT[::-1], _LON, amhara[::-1])

    assert flipped == map_figures.data_extent(_LAT, _LON, amhara)


def test_panels_share_the_smallest_view_that_holds_every_panels_data() -> None:
    north = map_figures.data_extent(_LAT, _LON, _field_with_data((11.0, 13.0), (36.0, 38.0)))
    south = map_figures.data_extent(_LAT, _LON, _field_with_data((9.0, 10.0), (38.0, 40.0)))

    shared = map_figures.covering([north, None, south])

    assert shared == map_figures.Extent(8.25, 13.75, 35.25, 40.75)
    assert map_figures.covering([None, None]) is None


# --- colour scale ----------------------------------------------------------------


def test_one_outlier_cell_does_not_stretch_the_colour_scale() -> None:
    errors = np.array([[4.0, 5.0, 6.0], [5.5, 4.5, 60.0], [np.nan, 5.0, 6.5]])

    scale = map_figures.robust_range([errors])

    assert (scale.vmin, scale.vmax) == (4.0, 6.5)
    assert scale.extend == "max" and scale.clipped


def test_a_field_without_outliers_keeps_its_full_range_and_an_unmarked_bar() -> None:
    errors = np.array([[4.0, 5.0, 6.0], [5.5, 4.5, 9.0]])

    scale = map_figures.robust_range([errors])

    assert (scale.vmin, scale.vmax, scale.extend) == (4.0, 9.0, "neither")
    assert not scale.clipped


def test_outliers_at_both_ends_mark_both_ends_of_the_bar() -> None:
    values = np.array([-50.0, 4.0, 4.5, 5.0, 5.5, 6.0, 80.0])

    assert map_figures.robust_range([values]).extend == "both"
    assert map_figures.robust_range([values[:-1]]).extend == "min"


def test_panels_share_one_scale_and_an_empty_map_has_none() -> None:
    aifs, fuxi = np.array([4.0, 5.0, 6.0]), np.array([7.0, 8.0, 9.0])

    shared = map_figures.robust_range([aifs, fuxi])

    assert (shared.vmin, shared.vmax) == (4.0, 9.0)
    assert map_figures.robust_range([np.array([np.nan])]) is None
    assert map_figures.robust_range([]) is None


def test_a_field_that_is_mostly_one_value_is_not_clipped_to_it() -> None:
    mostly_zero = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.3])

    scale = map_figures.robust_range([mostly_zero])

    assert (scale.vmin, scale.vmax, scale.extend) == (0.0, 0.3, "neither")


# --- figures saved from custom code ----------------------------------------------


def _saving_namespace(tmp_path: Path) -> dict:
    import os

    namespace = {"os": os, "ARTIFACT_DIR": str(tmp_path)}
    exec(code_harness.SAVE_FIGURE_SOURCE, namespace)  # noqa: S102 — the harness's own source
    return namespace


def _figure(title: str | None = None, heading: str | None = None, panels: int = 1):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, panels, squeeze=False)
    for ax in axes[0]:
        ax.set_title(title or "")
    if heading:
        fig.suptitle(heading)
    return fig


def test_a_figure_saved_from_code_keeps_the_title_it_was_given(tmp_path: Path) -> None:
    sandbox = _saving_namespace(tmp_path)

    sandbox["save_figure"](_figure("Drawn title"), "a.png", label="Onset error, Amhara zones")
    sandbox["save_figure"](_figure("Onset error by zone"), "b.png")
    sandbox["save_figure"](_figure("AIFS", heading="Onset error, days 1–15", panels=2), "c.png")
    sandbox["save_figure"](_figure(), "d.png")

    assert sandbox["FIGURE_TITLES"] == {
        "a.png": "Onset error, Amhara zones",
        "b.png": "Onset error by zone",
        "c.png": "Onset error, days 1–15",
    }
    assert {path.name for path in tmp_path.iterdir()} == {"a.png", "b.png", "c.png", "d.png"}


def test_files_written_by_code_are_listed_by_the_titles_their_figures_were_saved_with(
    tmp_path: Path,
) -> None:
    figure, table = tmp_path / "amhara_onset_error.png", tmp_path / "rows.csv"

    outputs = code_harness.derived_outputs(
        [figure, table], {"amhara_onset_error.png": "Onset error,\n Amhara zones " + "x" * 90}
    )
    untitled = code_harness.derived_outputs([figure], ["not", "a", "mapping"])

    assert [output.label for output in outputs] == ["derived_amhara_onset_error", "derived_rows"]
    assert outputs[0].title.startswith("Onset error, Amhara zones x")
    assert len(outputs[0].title) == workspaces.MAX_TITLE_LENGTH and outputs[0].title.endswith("…")
    assert outputs[1].title is None and untitled[0].title is None


# --- the map tools ---------------------------------------------------------------


def _write_skill_file(path: Path, level: float) -> Path:
    """A whole-Ethiopia skill grid scored only over a few zones, with one outlier cell."""
    errors = _field_with_data((9.0, 13.0), (36.0, 40.0)) * level / 5.0
    errors[np.isfinite(errors)] += np.linspace(0.0, 2.0, int(np.isfinite(errors).sum()))
    errors[14, 8] = 90.0
    xr.Dataset({"mean_mae": (("lat", "lon"), errors)}, coords={"lat": _LAT, "lon": _LON}).to_netcdf(
        path, engine="h5netcdf"
    )
    return path


async def _register_skill_file(user_id: str, wid: str, path: Path, label: str) -> None:
    _, [artifact] = await ledger.write_step(
        ledger.StepDraft(primitive="fetch.test", version="1.0.0", params={"source": path.name}),
        ledger.Attribution.by_user(user_id),
        [ledger.OutputFile(path=path, label=label, kind="dataset")],
    )
    await workspaces.add_artifacts(wid, [(artifact, label)])


async def _titles_listed(wid: str) -> dict[str, str]:
    return {item["label"]: item["title"] for item in await workspaces.listing(wid)}


@pytest.mark.asyncio
async def test_an_untitled_map_is_titled_cropped_and_scaled_without_the_models_help(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    path = _write_skill_file(tmp_path / "spatial_metrics_aifs_1-15.nc", 5.0)
    await _register_skill_file(user_id, wid, path, "spatial_metrics_aifs_1-15")
    ctx = tool_ctx(user_id, session["id"], wid)

    plotted = await figures.plot_map(ctx, artifact="spatial_metrics_aifs_1-15", variable="mean_mae")

    assert plotted.get("error") is None, plotted
    shown = plotted["shown"]
    assert shown["title"] == "Onset error, aifs · days 1–15"
    assert shown["extent"] == {
        "lat_min": 8.25,
        "lat_max": 13.75,
        "lon_min": 35.25,
        "lon_max": 40.75,
    }
    assert shown["colour_scale"]["clipped"] is True and shown["colour_scale"]["to"] < 10
    [figure] = plotted["workspace_artifacts"]
    assert (await _titles_listed(wid))[figure["label"]] == "Onset error, aifs · days 1–15"


@pytest.mark.asyncio
async def test_a_map_keeps_the_title_and_the_box_it_is_asked_for(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    path = _write_skill_file(tmp_path / "spatial_metrics_aifs_1-15.nc", 5.0)
    await _register_skill_file(user_id, wid, path, "spatial_metrics_aifs_1-15")
    box = FocusBox(lat_min=11.0, lat_max=13.0, lon_min=36.0, lon_max=38.0)

    plotted = await figures.plot_map(
        tool_ctx(user_id, session["id"], wid),
        artifact="spatial_metrics_aifs_1-15",
        variable="mean_mae",
        title="AIFS onset error, North Gondar, days 1–15",
        extent=box,
    )

    assert plotted["shown"]["title"] == "AIFS onset error, North Gondar, days 1–15"
    assert plotted["shown"]["extent"] == box.model_dump()
    assert plotted["shown"]["colour_scale"]["clipped"] is False, "the outlier is outside the box"


@pytest.mark.asyncio
async def test_side_by_side_maps_title_each_panel_in_words_on_one_view(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    for model, level in (("aifs", 5.0), ("fuxi", 8.0)):
        path = _write_skill_file(tmp_path / f"spatial_metrics_{model}_1-15.nc", level)
        await _register_skill_file(user_id, wid, path, f"spatial_metrics_{model}_1-15")
    ctx = tool_ctx(user_id, session["id"], wid)
    files = ["spatial_metrics_aifs_1-15", "spatial_metrics_fuxi_1-15"]

    plotted = await figures.plot_maps(ctx, artifacts=files, variable="mean_mae")
    renamed = await figures.plot_maps(
        ctx, artifacts=files, variable="mean_mae", panel_titles=["AIFS", "FuXi"]
    )
    mismatched = await figures.plot_maps(
        ctx, artifacts=files, variable="mean_mae", panel_titles=["AIFS"]
    )

    assert plotted.get("error") is None, plotted
    assert plotted["shown"]["title"] == "Onset error"
    assert plotted["shown"]["panels"] == ["aifs · days 1–15", "fuxi · days 1–15"]
    assert plotted["shown"]["extent"]["lat_max"] == 13.75
    assert plotted["shown"]["colour_scale"]["clipped"] is True
    assert renamed["shown"]["panels"] == ["AIFS", "FuXi"]
    assert "one panel title per artifact" in mismatched["error"]


@pytest.mark.asyncio
async def test_plotted_figures_point_at_their_workspace_artifact(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await register_file(user_id, wid, write_nc(tmp_path / "obs.nc"), "obs")
    plotted = await figures.plot_map(
        tool_ctx(user_id, session["id"], wid), artifact="obs", variable="far"
    )
    (figure,) = plotted["artifacts"]
    (registered,) = plotted["workspace_artifacts"]
    assert figure["workspace_artifact_id"] == registered["id"]
    described = await workspaces.describe(wid, await workspaces.resolve(wid, registered["label"]))
    [plot_step] = [s for s in described["steps"] if s["primitive"] == "figure.plot_map"]
    assert "artifact" not in plot_step["params"], "labels stay out of the ledger"


@pytest.mark.asyncio
async def test_plot_maps_puts_several_artifacts_in_one_stamped_figure(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await register_file(user_id, wid, write_nc(tmp_path / "a.nc", 1.0), "far_aifs")
    await register_file(user_id, wid, write_nc(tmp_path / "b.nc", 2.0), "far_fuxi")
    ctx = tool_ctx(user_id, session["id"], wid)

    plotted = await figures.plot_maps(ctx, artifacts=["far_aifs", "far_fuxi"], variable="far")
    assert plotted.get("error") is None, plotted
    (figure,) = plotted["workspace_artifacts"]
    response = await client.get(
        f"/workspaces/{wid}/artifacts/{figure['id']}/provenance", headers=as_new_user
    )
    lineage = response.json()["lineage"]
    assert "far_aifs" in lineage and "far_fuxi" in lineage

    one = await figures.plot_maps(ctx, artifacts=["far_aifs"], variable="far")
    assert "2 to 4" in one["error"]


@pytest.mark.asyncio
async def test_a_season_forecast_is_mapped_at_the_issue_date_nearest_the_one_asked_for(
    client, as_new_user, user_id
) -> None:
    from ai_almanac.server.services.forecast_pipeline import season_output_name
    from ai_almanac.server.services.storage import get_storage

    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, {"blend_id": "b1"}, job_type="forecast")
    write_season_nc(Path(get_storage().job_output_uri(job_id)[0]) / season_output_name("aifs"))
    await workspaces.link_job(workspace["id"], job_id)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    plotted = await figures.plot_map(
        ctx, artifact="aifs_season_forecast", variable="tp", select={"time": "2026-07-07"}
    )
    bad = await figures.plot_map(
        ctx, artifact="aifs_season_forecast", variable="tp", select={"step": "1"}
    )

    assert plotted.get("error") is None, plotted
    assert "not among dims" in bad["error"]
