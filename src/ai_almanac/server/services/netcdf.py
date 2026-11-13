"""Every NetCDF read and write in the server process, behind one lock.

HDF5, which backs NetCDF4 for both the netCDF4 and h5netcdf engines, is not
thread-safe, and the assistant's tool calls run in parallel threads: two files
open at once can crash the whole server. A dataset's lazy reads and its close
touch HDF5 too, so the lock is held for as long as the dataset is open. It is
reentrant, so code holding a dataset open can open another.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import xarray as xr

HDF5_LOCK = threading.RLock()


@contextmanager
def open_dataset(source, **kwargs) -> Iterator[xr.Dataset]:
    import xarray as xr

    with HDF5_LOCK, xr.open_dataset(source, **kwargs) as dataset:
        yield dataset


def load_dataset(source, **kwargs) -> xr.Dataset:
    """The whole file in memory, so nothing touches HDF5 after the lock is released."""
    import xarray as xr

    with HDF5_LOCK:
        return xr.load_dataset(source, **kwargs)


def write(data: xr.Dataset | xr.DataArray, path, **kwargs) -> None:
    with HDF5_LOCK:
        data.to_netcdf(path, **kwargs)
