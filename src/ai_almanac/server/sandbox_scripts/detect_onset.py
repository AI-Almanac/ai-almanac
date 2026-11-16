"""Onset by a ROMP onset definition, read from one model's raw season forecast.

Runs as `compute` in the workspace code sandbox, whose analysis environment has ROMP
(momp); the server never imports it. The steps follow ROMP's own forecast scoring
(momp.stats.detect): drop lead day 0, apply the rule over lead days 1 to
max_forecast_day plus its lookahead, and take the first lead day the rule finds.

Inputs: "forecast", a NetCDF of daily rainfall over (time, day, lat, lon), and
"definition", JSON with onset_rule, onset_rule_params (complete), wet_threshold,
max_forecast_day, variable, issue_dim and members. Writes onset_days.nc (lead day of onset per
issue date and cell, NaN when none) and onset_windows.csv (one row per cell and issue
date in the day-level probabilities layout, so area summaries can read it).
"""

import csv
import json
import os

import numpy as np
import xarray as xr
from momp.stats.onset_rule import first_onset_index, resolve_rule


def compute(inputs):
    with open(inputs["definition"]) as handle:
        definition = json.load(handle)
    rule = resolve_rule(definition["onset_rule"], definition["onset_rule_params"])
    last_day = int(definition["max_forecast_day"])
    with xr.open_dataset(inputs["forecast"]) as ds:
        rain = ds[definition["variable"]]
        names = {"day": "step", definition["issue_dim"]: "init_time"}
        rain = rain.rename({old: new for old, new in names.items() if old != new}).load()
    onset = onset_days(rain, rule, definition["wet_threshold"], last_day)
    out = os.environ["ARTIFACT_DIR"]
    write_onset_days(onset, rule, definition, os.path.join(out, "onset_days.nc"))
    write_windows(onset, last_day, definition["members"], os.path.join(out, "onset_windows.csv"))
    return summary(onset, last_day)


def onset_days(rain, rule, threshold, last_day):
    needed = last_day + rule.lookahead
    rain = rain.sel(step=slice(1, needed))
    if rain.sizes["step"] < needed:
        raise ValueError(
            f"The forecast holds {rain.sizes['step']} lead days; this definition needs {needed}"
        )
    mask = rule(rain.transpose("init_time", "lat", "lon", "step"), threshold, dim="step")
    mask = mask.where(mask.step <= last_day, False)
    index = first_onset_index(mask, "step")
    steps = mask.step.values.astype(float)
    days = np.where(index.values >= 0, steps[np.clip(index.values, 0, None)], np.nan)
    return xr.DataArray(days, dims=index.dims, coords=index.coords, name="onset_day")


def write_onset_days(onset, rule, definition, path):
    onset = onset.rename({"init_time": "time"})
    onset.attrs = {
        "long_name": "lead day of predicted onset (NaN: none within the forecast days)",
        "units": "days after issue",
        "onset_rule": rule.label(),
        "wet_threshold_mm": float(definition["wet_threshold"]),
        "max_forecast_day": int(definition["max_forecast_day"]),
    }
    onset.to_dataset().to_netcdf(path, engine="h5netcdf")


def write_windows(onset, last_day, members, path):
    columns = [f"p_day_{day}" for day in range(1, last_day + 1)] + ["p_later"]
    times = [str(t)[:10] for t in onset.init_time.values]
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id", "time", "lat", "lon", "members", *columns])
        for t, time in enumerate(times):
            for i, lat in enumerate(onset.lat.values):
                for j, lon in enumerate(onset.lon.values):
                    day = onset.values[t, i, j]
                    one_hot = [0] * len(columns)
                    one_hot[len(columns) - 1 if np.isnan(day) else int(day) - 1] = 1
                    writer.writerow(
                        [
                            f"{lat:.2f}_{lon:.2f}",
                            time,
                            f"{lat:.4f}",
                            f"{lon:.4f}",
                            members,
                            *one_hot,
                        ]
                    )


def summary(onset, last_day):
    latest = onset.isel(init_time=-1).values
    found = latest[~np.isnan(latest)]
    return {
        "issue_dates": int(onset.sizes["init_time"]),
        "cells": int(latest.size),
        "latest_issue_date": str(onset.init_time.values[-1])[:10],
        "latest_share_with_onset": round(found.size / latest.size, 3) if latest.size else 0,
        "latest_median_onset_day": float(np.median(found)) if found.size else None,
        "max_forecast_day": last_day,
    }
