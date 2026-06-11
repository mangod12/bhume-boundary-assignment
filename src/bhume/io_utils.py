"""IO helpers for GeoJSON and raster assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import json
import numpy as np

from .deps import geopandas, rasterio


def read_geojson(path: Path):
    """Load GeoJSON into GeoDataFrame."""
    if not path.exists():
        raise FileNotFoundError(f"Missing GeoJSON file: {path}")
    gpd = geopandas()
    return gpd.read_file(str(path))


def ensure_path(path: Path) -> Path:
    if not path:
        raise ValueError("Path is required.")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")
    return p


@dataclass
class RasterLoadResult:
    array: np.ndarray
    transform: object
    crs: object
    width: int
    height: int


def read_raster(path: Path) -> RasterLoadResult:
    if not path.exists():
        raise FileNotFoundError(f"Missing raster file: {path}")
    rio = rasterio()
    with rio.open(path) as src:
        band = src.read(1).astype(np.float32, copy=False)
        # fill nodata with a numeric neutral value so edge logic remains bounded
        if src.nodata is not None:
            band = np.where(band == src.nodata, np.nan, band)
        return RasterLoadResult(
            array=band,
            transform=src.transform,
            crs=src.crs,
            width=src.width,
            height=src.height,
        )


def write_predictions(path: Path, features: list[dict]) -> None:
    payload = {
        "type": "FeatureCollection",
        "features": features,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

