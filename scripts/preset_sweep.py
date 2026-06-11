#!/usr/bin/env python3
"""Iterative preset benchmark loop for BhūMe boundary solver."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYS = sys.executable
CLI = f"{SYS} -m src.bhume.cli"
VILLAGES: List[tuple[str, str, str]] = [
    ("vadnerbhairav", "34855_vadnerbhairav_chandavad_nashik", "34855_vadnerbhairav_chandavad_nashik/example_truths.geojson"),
    ("malatavadi", "12429_malatavadi_chandgad_kolhapur", "12429_malatavadi_chandgad_kolhapur/example_truths.geojson"),
]


def run_cmd(cmd: str, cwd: Path) -> None:
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or "command failed without output"
        raise RuntimeError(f"{cmd}\n{reason}")


def run_audit(project_root: Path) -> None:
    audit_file = project_root / "tools" / "last_audit.json"
    run_cmd("node tools/audit_assignment_site.mjs > tools/last_audit.json", project_root)
    audit = json.loads(audit_file.read_text(encoding="utf-8"))
    if not audit.get("allOk"):
        raise RuntimeError(f"Website audit failed for {audit.get('routesChecked')} routes.")


@dataclass(frozen=True)
class PresetResult:
    error: Optional[str]
    preset: str
    village: str
    corrected: int
    flagged: int
    global_corrected_ratio: float
    global_flagged_ratio: float
    corrected_ratio: float
    truth_corrected_ratio: float
    overlap_count: int
    overlap_ratio_pred: float
    overlap_ratio_truth: float
    mean_iou: float
    mean_area_delta: float
    runtime: float
    config: Dict[str, Any]


def evaluate_preset(project_root: Path, preset: str) -> List[PresetResult]:
    results: List[PresetResult] = []
    for village_key, folder, truth_path in VILLAGES:
        output_dir = project_root / "data" / "outputs" / "preset-sweeps" / preset / folder
        output_dir.mkdir(parents=True, exist_ok=True)

        pred_path = output_dir / "predictions.geojson"
        manifest_path = output_dir / "manifest.json"
        score_path = output_dir / "score.json"
        start = time.time()
        try:
            run_cmd(
                f"{CLI} solve --village data/raw/{folder} --out {pred_path.as_posix()} "
                f"--manifest {manifest_path.as_posix()} --preset {preset} --include-flagged",
                project_root,
            )
            run_cmd(
                f"{CLI} score --predictions {pred_path.as_posix()} --truth data/raw/{truth_path} "
                f"--out {score_path.as_posix()}",
                project_root,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            score = json.loads(score_path.read_text(encoding="utf-8"))
            truth_metrics = {
                "truth_corrected_ratio": float(score.get("overlap_corrected_ratio", score["corrected_ratio"])),
                "overlap_count": int(score.get("overlap_count", 0)),
                "overlap_ratio_pred": float(score.get("overlap_ratio_predictions", score.get("overlap_ratio_pred", 0.0))),
                "overlap_ratio_truth": float(score.get("overlap_ratio_truth", 0.0)),
            }
            runtime = time.time() - start
            results.append(
                PresetResult(
                    error=None,
                    preset=preset,
                    village=village_key,
                    corrected=manifest["counts"]["corrected"],
                    flagged=manifest["counts"]["flagged"],
                    global_corrected_ratio=manifest["counts"]["corrected"] / manifest["counts"]["total"],
                    global_flagged_ratio=manifest["counts"]["flagged"] / manifest["counts"]["total"],
                    corrected_ratio=score["corrected_ratio"],
                    truth_corrected_ratio=truth_metrics["truth_corrected_ratio"],
                    overlap_count=truth_metrics["overlap_count"],
                    overlap_ratio_pred=truth_metrics["overlap_ratio_pred"],
                    overlap_ratio_truth=truth_metrics["overlap_ratio_truth"],
                    mean_iou=score["mean_iou"],
                    mean_area_delta=score["mean_area_delta"],
                    runtime=runtime,
                    config=manifest.get("config", {}),
                )
            )
        except RuntimeError as exc:
            runtime = time.time() - start
            results.append(
                PresetResult(
                    error=str(exc),
                    preset=preset,
                    village=village_key,
                    corrected=0,
                    flagged=0,
                    global_corrected_ratio=0.0,
                    global_flagged_ratio=0.0,
                    corrected_ratio=0.0,
                    overlap_count=0,
                    overlap_ratio_pred=0.0,
                    overlap_ratio_truth=0.0,
                    truth_corrected_ratio=0.0,
                    mean_iou=0.0,
                    mean_area_delta=999.0,
                    runtime=runtime,
                    config={"preset": preset},
                )
            )
    return results


def recommend(results: List[PresetResult]) -> str:
    by_preset: Dict[str, List[PresetResult]] = {}
    for item in results:
        if item.error is not None:
            continue
        by_preset.setdefault(item.preset, []).append(item)

    # Robustness-oriented score across both villages:
    # keep good overlap, low geometry drift, and avoid over-correcting everything.
    scored = {}
    for preset, values in by_preset.items():
        if len(values) != len(VILLAGES):
            continue
        mean_iou = sum(v.mean_iou for v in values) / len(values)
        mean_area = sum(v.mean_area_delta for v in values) / len(values)
        mean_ratio = sum(v.global_corrected_ratio for v in values) / len(values)
        mean_truth = sum(v.truth_corrected_ratio for v in values) / len(values)
        mean_truth_overlap = sum(v.overlap_ratio_truth for v in values) / len(values)
        target_ratio = 0.06
        ratio_delta = abs(mean_ratio - target_ratio)
        overlap_penalty = max(0.0, 0.30 - mean_truth_overlap) * 1.2
        score = (
            0.90 * mean_iou
            - 0.30 * mean_area
            - 3.5 * ratio_delta
            + 0.20 * mean_truth
            - overlap_penalty
            - 0.10 * (village_penalty(values))
        )
        scored[preset] = (
            score,
            mean_truth,
            mean_area,
            mean_ratio,
            mean_iou,
            mean_truth_overlap,
        )

    if not scored:
        raise RuntimeError("No successful preset results produced for recommendation.")

    ranked = sorted(scored.items(), key=lambda item: (-item[1][0], -item[1][1], item[1][2]))
    return ranked[0][0]


def village_penalty(values: List[PresetResult]) -> float:
    """Simple spread penalty to reduce uneven behavior across villages."""
    if not values:
        return 0.0
    ratios = [v.global_corrected_ratio for v in values]
    return max(ratios) - min(ratios)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate solver presets across both villages.")
    parser.add_argument(
        "--presets",
        nargs="*",
        default=["conservative", "balanced", "aggressive", "golden"],
        help="Preset names to evaluate.",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip live Playwright assignment page audit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable ranking JSON.",
    )
    args = parser.parse_args()

    if not args.no_audit:
        run_audit(PROJECT_ROOT)

    all_results: List[PresetResult] = []
    for preset in args.presets:
        all_results.extend(evaluate_preset(PROJECT_ROOT, preset))

    rec = recommend(all_results)
    if args.json:
        payload = {
            "recommended": rec,
            "results": [r.__dict__ for r in all_results],
        }
        print(json.dumps(payload, indent=2))
    else:
        print("Recommendation:", rec)
        for r in all_results:
            if r.error is not None:
                print(f"{r.preset} {r.village}: FAILED - {r.error}")
                continue
            print(
                f"{r.preset} {r.village}: corrected={r.corrected} flagged={r.flagged} "
                f"global_corrected_ratio={r.global_corrected_ratio:.3f} "
                f"truth_overlap={r.overlap_ratio_truth:.2f} "
                f"truth_corrected={r.truth_corrected_ratio:.3f} iou={r.mean_iou:.3f} "
                f"area_delta={r.mean_area_delta:.4f} runtime_s={r.runtime:.1f}"
            )
        print("recommended=", rec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
