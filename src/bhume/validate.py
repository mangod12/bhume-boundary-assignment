"""Submission output validation helpers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from .deps import geopandas


ALLOWED_STATUS = {"corrected", "flagged"}


def validate_predictions_file(predictions_path: Path, *, plot_id_field: str = "plot_number") -> Dict[str, object]:
    """Validate a predictions GeoJSON against the assignment contract.

    Returns a compact quality report that is useful for humans and automation.
    """
    gpd = geopandas()
    gdf = gpd.read_file(str(predictions_path))
    if len(gdf) == 0:
        raise ValueError("No features found in predictions output.")

    if plot_id_field not in gdf.columns:
        available = ", ".join(sorted(gdf.columns))
        raise ValueError(
            f"Missing required id field '{plot_id_field}'. "
            f"Available columns: {available}"
        )

    required = {"status", "confidence", "method_note", plot_id_field}
    missing = sorted(required - set(gdf.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    gdf["plot_number"] = gdf[plot_id_field]
    dup_count = int(gdf["plot_number"].duplicated().sum())
    if dup_count:
        raise ValueError(f"Duplicate plot IDs found for '{plot_id_field}': {dup_count}")

    status_invalid = gdf.loc[~gdf["status"].isin(ALLOWED_STATUS)]
    if len(status_invalid):
        raise ValueError(f"Invalid status values found: {sorted(set(status_invalid['status']))}")

    confidence_invalid = gdf.loc[~gdf["confidence"].between(0.0, 1.0)]
    if len(confidence_invalid):
        raise ValueError(f"Out-of-range confidence values found in {len(confidence_invalid)} rows.")

    method_missing = gdf.loc[gdf["method_note"].isna() | (gdf["method_note"] == "")]
    if len(method_missing):
        raise ValueError(f"method_note missing/empty in {len(method_missing)} rows.")

    null_geoms = int(gdf.geometry.isna().sum())
    invalid_geoms = int((~gdf.is_valid.fillna(False)).sum())
    validity = null_geoms == 0 and invalid_geoms == 0

    status_counts = Counter(gdf["status"].tolist())
    corrected_count = status_counts.get("corrected", 0)
    flagged_count = status_counts.get("flagged", 0)
    total = len(gdf)
    if corrected_count + flagged_count != total:
        raise ValueError("Status column does not cover all rows.")

    return {
        "total": total,
        "corrected": int(corrected_count),
        "flagged": int(flagged_count),
        "corrected_ratio": round(corrected_count / total, 6) if total else 0.0,
        "flagged_ratio": round(flagged_count / total, 6) if total else 0.0,
        "plot_id_field": plot_id_field,
        "status_counts": dict(status_counts),
        "geometry": {
            "null": null_geoms,
            "invalid": invalid_geoms,
            "valid": validity,
        },
        "valid": bool(validity),
    }


def maybe_resolve_plot_id_field(columns: List[str], preferred: Optional[str] = None) -> str:
    candidates = [preferred] if preferred else []
    candidates.extend(["plot_number", "plot_id", "plot", "pid", "id"])
    for name in candidates:
        if not name:
            continue
        if name in columns:
            return name
    if "ID" in columns:
        return "ID"
    if "plotid" in columns:
        return "plotid"
    raise ValueError(f"Unable to resolve plot id field from columns: {columns}")
