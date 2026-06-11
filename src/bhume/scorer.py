"""Scoring helper for local sanity checks against example truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict
from collections import Counter

from .deps import geopandas


def score_predictions(
    predictions_path: Path,
    truth_path: Path,
    out_path: Path | None = None,
) -> Dict[str, float]:
    gpd = geopandas()
    pred = gpd.read_file(str(predictions_path))
    truth = gpd.read_file(str(truth_path))
    total_predictions = len(pred)
    total_truth = len(truth)

    if "plot_number" not in pred.columns:
        raise ValueError("predictions must include plot_number")
    if "plot_number" not in truth.columns:
        raise ValueError("truth must include plot_number")

    pred = pred.set_index("plot_number")
    truth = truth.set_index("plot_number")
    common = pred.index.intersection(truth.index)
    overlap_count = len(common)
    if overlap_count == 0:
        raise ValueError("No common plot_number between predictions and truth.")

    pred = pred.loc[common]
    truth = truth.loc[common]

    if pred.crs is None and truth.crs is None:
        pred = pred.set_crs("EPSG:4326")
        truth = truth.set_crs("EPSG:4326")
    elif pred.crs is None:
        pred = pred.set_crs(truth.crs)
    elif truth.crs is None:
        truth = truth.set_crs(pred.crs)

    pred = pred.to_crs("EPSG:3857")
    truth = truth.to_crs("EPSG:3857")

    status_values = pred.get("status", [])
    status_counts = Counter(status_values)
    known_status = {"corrected", "flagged"}
    unknown = set(status_values) - known_status
    if unknown:
        raise ValueError(f"Invalid status values found: {sorted(unknown)}")

    invalid_conf = pred.loc[~pred["confidence"].between(0.0, 1.0)]
    if len(invalid_conf):
        raise ValueError(f"Found out-of-range confidence values in {len(invalid_conf)} features.")

    missing_method_note = pred.loc[pred["method_note"].isna() | (pred["method_note"] == "")]
    if len(missing_method_note):
        raise ValueError("method_note is required and cannot be empty.")

    pred["area_pred"] = pred.area
    truth["area_truth"] = truth.area
    pred["area_truth"] = truth.loc[pred.index, "area_truth"]
    pred["union_area"] = pred.geometry.union(truth.geometry).area
    pred["inter_area"] = pred.geometry.intersection(truth.geometry).area
    pred["iou"] = pred["inter_area"] / pred["union_area"].replace(0, 1.0)
    pred["area_delta"] = (pred["area_pred"] - pred["area_truth"]).abs() / pred["area_truth"].replace(0, 1.0)
    pred["corrected"] = (pred.get("status") == "corrected").astype(int)

    n = len(pred)
    mean_iou = float(pred["iou"].mean())
    mean_area_delta = float(pred["area_delta"].mean())
    corrected_ratio = float(pred["corrected"].mean())
    corrected_subset = pred.loc[pred["corrected"] == 1]
    corrected_iou = float(corrected_subset["iou"].mean()) if len(corrected_subset) else 0.0
    flagged_ratio = 1.0 - corrected_ratio
    overlap_corrected_ratio = corrected_ratio
    results = {
        "total_predictions": int(total_predictions),
        "total_truth": int(total_truth),
        "overlap_count": int(overlap_count),
        "overlap_ratio_predictions": overlap_count / total_predictions if total_predictions else 0.0,
        "overlap_ratio_truth": overlap_count / total_truth if total_truth else 0.0,
        "total": int(n),
        "mean_iou": mean_iou,
        "mean_area_delta": mean_area_delta,
        "corrected_ratio": corrected_ratio,
        "overlap_corrected_ratio": overlap_corrected_ratio,
        "flagged_ratio": flagged_ratio,
        "corrected_mean_iou": corrected_iou,
    }

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2)
    return results
