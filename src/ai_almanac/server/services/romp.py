from __future__ import annotations

from pathlib import Path


def _date_tuple(value: str) -> tuple[int, int, int]:
    year, month, day = value.split("-")
    return int(year), int(month), int(day)


def _members(value: object) -> str | tuple[int, ...]:
    if value is None or str(value).strip().lower() == "all":
        return "All"
    return tuple(int(member.strip()) for member in str(value).split(",") if member.strip())


def _init_days(value: object) -> tuple[int, ...]:
    return tuple(int(day.strip()) for day in str(value or "0,3").split(",") if day.strip())


def render_romp_config(config: dict, output_dir: Path, figure_dir: Path) -> str:
    params = config.get("romp_params") or {}
    model = config.get("model_config") or {}
    dataset = config.get("dataset_config") or {}

    obs_dir = str(config["obs_dir"])
    model_dir = str(config["model_dir"])
    model_name = str(config["model_name"])
    obs_pattern = str(params.get("obs_file_pattern") or dataset.get("obs_file_pattern") or "{}.nc")
    obs_var = str(params.get("obs_var") or dataset.get("obs_var") or "RAINFALL")
    model_pattern = str(params.get("file_pattern") or model.get("file_pattern") or "{}.nc")
    model_var = str(params.get("model_var") or model.get("model_var") or "tp")
    start_date = _date_tuple(str(params.get("start_date") or model.get("start_date")))
    end_date = _date_tuple(str(params.get("end_date") or model.get("end_date")))
    ref_model_dir = str(params.get("ref_model_dir") or obs_dir)

    values = {
        "project_name": f"AI Almanac job {config.get('job_id', '')}".strip(),
        "work_dir": str(output_dir),
        "pkg_dir": str(output_dir),
        "layout": ("model", "verification_window"),
        "model_list": (model_name,),
        "obs": str(params.get("obs") or dataset.get("source_name") or "observations"),
        "obs_dir": obs_dir,
        "obs_file_pattern": (obs_pattern,),
        "obs_var": obs_var,
        "obs_unit_cvt": dataset.get("unit_cvt"),
        "ref_model": str(params.get("ref_model") or "climatology"),
        "ref_model_dir": ref_model_dir,
        "ref_model_file_pattern": obs_pattern,
        "ref_model_var": obs_var,
        "ref_model_unit_cvt": dataset.get("unit_cvt"),
        "model_dir_list": (model_dir,),
        "model_var_list": (model_var,),
        "unit_cvt_list": (model.get("unit_cvt"),),
        "file_pattern_list": (model_pattern,),
        "region": str(params.get("region") or config.get("romp_region") or "Ethiopia"),
        "lat_min": params.get("lat_min"),
        "lat_max": params.get("lat_max"),
        "lon_min": params.get("lon_min"),
        "lon_max": params.get("lon_max"),
        "nc_mask": params.get("nc_mask"),
        "land_only": bool(params.get("land_only", True)),
        "shp_only": bool(params.get("shp_only", True)),
        "shpfile_dir": None,
        "polygon": False,
        "wet_init": params.get("wet_init", 1),
        "wet_threshold": params.get("wet_threshold", 20),
        "wet_spell": params.get("wet_spell", 3),
        "dry_threshold": 1,
        "dry_spell": params.get("dry_spell", 7),
        "dry_extent": params.get("dry_extent", 0),
        "thresh_file": params.get("thresh_file"),
        "thresh_var": None,
        "onset_percentage_threshold": 0.5,
        "start_date": start_date,
        "end_date": end_date,
        "start_year_clim": int(params.get("start_year_clim") or start_date[0]),
        "end_year_clim": int(params.get("end_year_clim") or end_date[0]),
        "init_days": _init_days(params.get("init_days")),
        "date_filter_year": int(params.get("date_filter_year") or start_date[0]),
        "verification_window_list": ((1, 15), (16, 30)),
        "tolerance_days_list": (3, 5),
        "max_forecast_day": int(params.get("max_forecast_day") or 30),
        "day_bins": ((1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30)),
        "FAR": True,
        "MAE": True,
        "MR": True,
        "probabilistic": bool(params.get("probabilistic", False)),
        "members": _members(params.get("members")),
        "BS": True,
        "RPS": True,
        "AUC": True,
        "Reliability": True,
        "skill_score": True,
        "dir_out": str(output_dir),
        "dir_fig": str(figure_dir),
        "save_fig": True,
        "save_nc_spatial_far_mr_mae": True,
        "save_csv_score": True,
        "save_nc_climatology": True,
        "plot_spatial_far_mr_mae": False,
        "plot_heatmap_bss_auc": False,
        "plot_reliability": False,
        "plot_climatology_onset": False,
        "plot_panel_heatmap_error": False,
        "plot_panel_heatmap_skill": False,
        "plot_bar_bss_rpss_auc": False,
        "show_plot": False,
        "show_panel": False,
        "parallel": bool(params.get("parallel", not params.get("probabilistic", False))),
        "debug": False,
    }
    return "\n".join(f"{key} = {value!r}" for key, value in values.items()) + "\n"


def write_romp_config(job_id: str, config: dict, output_dir: Path, figure_dir: Path) -> Path:
    job_config = {**config, "job_id": job_id}
    path = output_dir.parent / "romp-config.in"
    path.write_text(render_romp_config(job_config, output_dir, figure_dir))
    return path
