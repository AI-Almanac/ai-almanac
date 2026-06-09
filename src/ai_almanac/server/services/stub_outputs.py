"""Synthetic ROMP-shaped outputs for the stub runner.

Used when `RUNNER_MODE != 'pixi'` to produce valid-shaped NetCDF metrics and
placeholder figures so the full UI flow (submit → status → metrics map →
figures) works end-to-end without the benchmark environment installed. The
output schema matches what ROMP itself produces, so downstream metrics/map/
figure code does not distinguish stub from real.
"""

from __future__ import annotations

from pathlib import Path

# The forecast windows ROMP normally emits; mirrored so the window picker has
# real options to render.
WINDOWS = ("1-7", "8-14", "15-21", "22-30")


def resolve_grid(config: dict) -> tuple[list[float], list[float]]:
    """Match the input obs grid when possible; otherwise a 10x10 stub grid."""
    import numpy as np

    obs_dir = config.get("obs_dir")
    if obs_dir:
        try:
            from glob import glob

            ncs = sorted(glob(str(Path(obs_dir) / "*.nc")))
            if ncs:
                import xarray as xr

                with xr.open_dataset(ncs[0]) as ds:
                    for lat_name in ("lat", "latitude", "LAT", "LATITUDE"):
                        if lat_name in ds.coords or lat_name in ds.dims:
                            lat_vals = ds[lat_name].values.tolist()
                            break
                    else:
                        lat_vals = list(np.linspace(-10, 10, 10))
                    for lon_name in ("lon", "longitude", "LON", "LONGITUDE"):
                        if lon_name in ds.coords or lon_name in ds.dims:
                            lon_vals = ds[lon_name].values.tolist()
                            break
                    else:
                        lon_vals = list(np.linspace(30, 50, 10))
                    return lat_vals, lon_vals
        except Exception:
            pass
    return (
        list(np.linspace(-10, 10, 10)),
        list(np.linspace(30, 50, 10)),
    )


def write_metric_nc(
    out_path: Path,
    lat: list[float],
    lon: list[float],
    model_name: str,
    window: str,
) -> None:
    import numpy as np
    import xarray as xr

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(hash((model_name, window)) & 0xFFFFFFFF)
    shape = (len(lat), len(lon))

    # Plausible bounded values per metric so the map renders meaningfully.
    values = {
        "false_alarm_rate": rng.uniform(0.0, 0.6, size=shape),
        "miss_rate": rng.uniform(0.0, 0.5, size=shape),
        "mae": rng.uniform(0.5, 5.0, size=shape),
        "rmse": rng.uniform(0.5, 6.0, size=shape),
        "bias": rng.uniform(-2.0, 2.0, size=shape),
    }
    ds = xr.Dataset(
        {name: (("lat", "lon"), arr) for name, arr in values.items()},
        coords={"lat": lat, "lon": lon},
        attrs={
            "model": model_name,
            "verification_window": window,
            "source": "ai-almanac stub runner — synthetic values, not real metrics",
        },
    )
    ds.to_netcdf(out_path)


def write_placeholder_figure(path: Path, model_name: str, figure_name: str) -> None:
    """Render a tiny matplotlib image so the figure viewer has something to show."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(figsize=(6, 4))
        ax.imshow(
            np.random.default_rng(hash(figure_name) & 0xFFFFFFFF).random((20, 30)),
            cmap="viridis",
        )
        ax.set_title(f"[STUB] {figure_name} — {model_name}")
        ax.axis("off")
        fig.tight_layout()
        fig.savefig(path, dpi=80)
        plt.close(fig)
    except Exception:
        # If matplotlib isn't installed, drop a tiny PNG so the link works.
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
            b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9c"
            b"c\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
