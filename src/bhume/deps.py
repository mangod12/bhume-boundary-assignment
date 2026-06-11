"""Dependency loading helpers.

The codebase must run in environments where optional geo libraries may be missing.
These helpers provide clear, actionable errors before any heavy import occurs.
"""

from __future__ import annotations

from typing import Type, TypeVar

_T = TypeVar("_T")


def require_dependency(name: str, import_error_hint: str) -> Type[_T]:
    """Import and return a module with a clear install hint."""
    try:
        __import__(name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(import_error_hint) from exc
    return __import__(name)


def geopandas():
    return require_dependency(
        "geopandas",
        (
            "geopandas is required. Run `pip install -r requirements.txt` "
            "inside bhume-boundary-assignment."
        ),
    )


def rasterio():
    return require_dependency(
        "rasterio",
        (
            "rasterio is required for imagery/boundaries alignment. "
            "Run `pip install -r requirements.txt` inside bhume-boundary-assignment."
        ),
    )


def shapely():
    return require_dependency(
        "shapely",
        (
            "shapely is required for geometry transforms and validity checks. "
            "Run `pip install -r requirements.txt` inside bhume-boundary-assignment."
        ),
    )


def pyproj():
    return require_dependency(
        "pyproj",
        (
            "pyproj is required for robust area/transform handling. "
            "Run `pip install -r requirements.txt` inside bhume-boundary-assignment."
        ),
    )

