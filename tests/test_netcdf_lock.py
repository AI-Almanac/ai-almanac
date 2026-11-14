"""The server reads and writes NetCDF one file at a time: HDF5 crashes the process
when two threads use it at once, as the assistant's parallel tool calls do."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np
import xarray as xr

from ai_almanac.server.services import netcdf

SERVER = Path(__file__).resolve().parents[1] / "src" / "ai_almanac" / "server"
# Each runs in a job's own process, never in a server thread.
OWN_PROCESS = {
    "services/forecast_pipeline.py",
    "services/stub_outputs.py",
    "services/e2s.py",
}
# Scripts here run inside the code sandbox.
SANDBOX_SCRIPTS = Path("sandbox_scripts")
DIRECT_NETCDF = re.compile(
    r"xr\.(open_dataset|open_dataarray|open_mfdataset|load_dataset)\(|\.to_netcdf\("
)


def _sample(path: Path) -> Path:
    netcdf.write(xr.Dataset({"tp": ("lat", np.arange(3.0))}, coords={"lat": [0, 1, 2]}), path)
    return path


def test_a_second_open_waits_until_the_first_dataset_is_closed(tmp_path: Path) -> None:
    first, second = _sample(tmp_path / "a.nc"), _sample(tmp_path / "b.nc")
    events: list[str] = []
    holding = threading.Event()

    def read_second() -> None:
        holding.wait()
        with netcdf.open_dataset(second) as ds:
            events.append(f"second read {float(ds.tp.sum())}")

    reader = threading.Thread(target=read_second)
    reader.start()
    with netcdf.open_dataset(first) as ds:
        holding.set()
        reader.join(timeout=0.3)
        events.append(f"first read {float(ds.tp.sum())}")
    reader.join(timeout=5)

    assert events == ["first read 3.0", "second read 3.0"]


def test_a_dataset_held_open_can_open_another_on_the_same_thread(tmp_path: Path) -> None:
    first, second = _sample(tmp_path / "a.nc"), _sample(tmp_path / "b.nc")

    with netcdf.open_dataset(first) as a, netcdf.open_dataset(second) as b:
        assert float(a.tp.sum() + b.tp.sum()) == 6.0


def test_server_code_reaches_netcdf_only_through_the_shared_lock() -> None:
    direct = [
        f"{path.relative_to(SERVER)}:{n}"
        for path in SERVER.rglob("*.py")
        if path.name != "netcdf.py"
        and str(path.relative_to(SERVER)) not in OWN_PROCESS
        and SANDBOX_SCRIPTS not in path.relative_to(SERVER).parents
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if DIRECT_NETCDF.search(line)
    ]
    assert direct == [], "open and write NetCDF through services.netcdf"
