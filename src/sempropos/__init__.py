"""sempropos package."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("sempropos")
except PackageNotFoundError:
    __version__ = "0.1.0"
