"""ai-almanac: local-first benchmarking platform for AI weather/climate models."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ai-almanac")
except PackageNotFoundError:
    # Imported by a workload env that has the package on its path but not
    # installed (forecast_entrypoint).
    __version__ = "0+unknown"
