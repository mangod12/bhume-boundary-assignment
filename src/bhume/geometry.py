"""Geometry helpers with conservative fallbacks."""

from __future__ import annotations

from dataclasses import dataclass

import math
from typing import Optional

from .config import AREA_FIELDS
from .deps import shapely, pyproj

gpd_crs = "EPSG:4326"


def _normalize_area_field(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def detect_area_field(properties: dict) -> Optional[str]:
    for key in AREA_FIELDS:
        if key in properties and _normalize_area_field(properties.get(key)) is not None:
            return key
    return None


@dataclass(frozen=True)
class AreaEstimate:
    provided: Optional[float]
    polygon_sq_m: Optional[float]


def pick_utm_epsg(lon: float, lat: float) -> str:
    zone = int((lon + 180.0) // 6 + 1)
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return f"EPSG:{epsg}"


def geometry_area_square_meters(geometry, crs) -> Optional[float]:
    """Estimate geometry area in square meters, with fallback when CRS is unavailable."""
    if geometry is None:
        return None
    if getattr(geometry, "is_empty", True):
        return None

    shapely_geom = shapely()
    if crs is not None and str(crs).lower() != "epsg:4326":
        try:
            return float(geometry.area)
        except Exception:
            return None

    try:
        centroid = geometry.centroid
        utm = pick_utm_epsg(centroid.x, centroid.y)
        from shapely.ops import transform

        transformer = pyproj().Transformer.from_crs(
            "EPSG:4326",
            utm,
            always_xy=True,
        )
        projected = transform(transformer.transform, geometry)
        return float(projected.area)
    except Exception:
        return None


def area_and_field(row) -> AreaEstimate:
    props = row.get("properties", {}) if isinstance(row, dict) else getattr(row, "to_dict", lambda: {})()
    if hasattr(props, "to_dict"):
        props = props.to_dict()
    field = detect_area_field(props)
    provided = _normalize_area_field(props.get(field)) if field else None
    geometry = getattr(row, "geometry", None) if not isinstance(row, dict) else row.get("geometry")
    return AreaEstimate(provided=provided, polygon_sq_m=geometry_area_square_meters(geometry, None))


def clamp_confidence(value: float) -> float:
    if value is None:
        return 0.0
    return min(0.99, max(0.0, float(value)))

