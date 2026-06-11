#!/usr/bin/env python3
"""Run evidence-source ablations on the public truth subset.

Full-village ablation with dense candidate-grid search is intentionally avoided:
the final predictions still run full-village, while ablation focuses on the
publicly verifiable truth rows so reviewers can inspect signal behavior quickly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd

from bhume.config import SolverConfig
from bhume.scorer import score_predictions_with_baseline
from bhume.solver import BoundarySolver


ROOT = Path(__file__).resolve().parent.parent
VILLAGES = [
    "34855_vadnerbhairav_chandavad_nashik",
    "12429_malatavadi_chandgad_kolhapur",
]
SIGNAL_MODES = ["imagery_only", "boundaries_only", "imagery_plus_boundaries"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BhuMe public-truth signal ablations.")
    parser.add_argument("--data-root", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--out-root", type=Path, default=ROOT / "data" / "outputs" / "ablation")
    parser.add_argument("--preset", default="golden")
    return parser.parse_args()


def write_truth_subset(village_dir: Path, out_dir: Path) -> Path:
    source = gpd.read_file(str(village_dir / "input.geojson"))
    truth = gpd.read_file(str(village_dir / "example_truths.geojson"))
    truth_ids = set(truth["plot_number"].astype(str))
    subset = source[source["plot_number"].astype(str).isin(truth_ids)].copy()
    if len(subset) == 0:
        raise RuntimeError(f"No public truth rows found in {village_dir}")
    out_path = out_dir / "input.geojson"
    subset.to_file(str(out_path), driver="GeoJSON")
    return out_path


def solve_subset(village_dir: Path, input_path: Path, out_dir: Path, mode: str, preset: str):
    pred_path = out_dir / "predictions.geojson"
    manifest_path = out_dir / "manifest.json"
    config = SolverConfig.from_dict(
        {
            "preset": preset,
            "include_flagged": True,
        },
        village_name=village_dir.name,
    )
    solver = BoundarySolver(config)
    imagery = village_dir / "imagery.tif"
    boundaries = village_dir / "boundaries.tif"
    if mode == "imagery_only":
        boundaries = None
    elif mode == "boundaries_only":
        imagery = village_dir / "boundaries.tif"
        boundaries = None
    solver.run(
        input_geojson=input_path,
        output_geojson=pred_path,
        imagery=imagery,
        boundaries=boundaries,
        manifest_path=manifest_path,
        include_flagged=True,
    )
    return pred_path, manifest_path


def main() -> int:
    args = parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)
    rows = []

    for mode in SIGNAL_MODES:
        for village in VILLAGES:
            village_dir = args.data_root / village
            out_dir = args.out_root / mode / village
            out_dir.mkdir(parents=True, exist_ok=True)
            subset_input = write_truth_subset(village_dir, out_dir)
            pred_path, manifest_path = solve_subset(village_dir, subset_input, out_dir, mode, args.preset)
            score_path = out_dir / "score.json"
            score = score_predictions_with_baseline(
                pred_path,
                subset_input,
                village_dir / "example_truths.geojson",
                score_path,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "signal_mode": mode,
                    "village": village,
                    "scope": "public_truth_subset",
                    "counts": manifest["counts"],
                    "baseline_mean_iou": score.get("baseline_mean_iou"),
                    "prediction_mean_iou": score.get("prediction_mean_iou"),
                    "mean_iou_delta": score.get("mean_iou_delta"),
                    "corrected_mean_delta": score.get("corrected_mean_delta"),
                    "flagged_mean_delta": score.get("flagged_mean_delta"),
                }
            )

    summary = {
        "preset": args.preset,
        "scope": "public_truth_subset",
        "signal_modes": SIGNAL_MODES,
        "villages": VILLAGES,
        "results": rows,
    }
    (args.out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
