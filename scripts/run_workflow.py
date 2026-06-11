#!/usr/bin/env python3
"""Run the full BhuMe assignment workflow in one reproducible command."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI = [sys.executable, "-m", "src.bhume.cli"]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUT_ROOT = PROJECT_ROOT / "data" / "outputs" / "workflow"


def run_cmd(cmd: List[str], *, cwd: Path, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    """Run one command and raise a rich error if it fails."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or "command failed without output."
        raise RuntimeError(f"command failed ({result.args}): {reason}")
    return result


def run_assignment_audit(base_url: str, project_root: Path) -> Dict[str, Any]:
    """Run Playwright page checks and return parsed audit JSON."""
    output_path = project_root / "tools" / "last_audit.json"
    env = os.environ.copy()
    env["BHUME_BASE_URL"] = base_url
    run_cmd(
        ["node", "tools/audit_assignment_site.mjs"],
        cwd=project_root,
        env=env,
    )
    if not output_path.exists():
        raise RuntimeError("Playwright audit did not write tools/last_audit.json.")
    audit = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(audit, dict):
        raise RuntimeError("Audit output had unexpected format.")
    return audit


TRUTH_CANDIDATES = ("example_truths.geojson", "truth.geojson", "truths.geojson")


def discover_case_dirs(data_root: Path, include: Optional[List[str]]) -> List[Path]:
    if not data_root.exists():
        raise RuntimeError(f"Data root does not exist: {data_root}")
    if include:
        requested = [value.strip().lower() for value in include if value and value.strip()]
        candidates = []
        for marker in requested:
            matches = [
                p
                for p in data_root.iterdir()
                if p.is_dir() and (p.name.lower() == marker or marker in p.name.lower())
            ]
            if not matches:
                raise RuntimeError(f"Requested dataset '{marker}' not found under {data_root}")
            if len(matches) > 1:
                raise RuntimeError(
                    f"Ambiguous dataset selector '{marker}'. Matches: {[p.name for p in matches]}"
                )
            candidates.append(matches[0])
        return sorted(candidates)

    cases = [
        p
        for p in data_root.iterdir()
        if p.is_dir()
        and (p / "input.geojson").exists()
        and (p / "imagery.tif").exists()
    ]
    if not cases:
        raise RuntimeError(f"No valid villages found in {data_root}.")
    return sorted(cases)


def find_truth_path(village_dir: Path) -> Optional[Path]:
    for name in TRUTH_CANDIDATES:
        path = village_dir / name
        if path.exists():
            return path
    for candidate in village_dir.glob("*truth*.geojson"):
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class CaseResult:
    village: str
    input_path: str
    output_dir: str
    status: str
    elapsed_seconds: float
    counts: Dict[str, Any]
    score: Optional[Dict[str, Any]]
    error: Optional[str] = None


def run_case(
    village_dir: Path,
    *,
    preset: str,
    output_root: Path,
    run_score: bool,
    include_flagged: bool,
) -> CaseResult:
    if include_flagged is True:
        include_text = "include-flagged"
    else:
        include_text = None

    out_dir = output_root / village_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    pred_path = out_dir / "predictions.geojson"
    manifest_path = out_dir / "manifest.json"
    score_path = out_dir / "score.json"

    cmd = [
        *CLI,
        "solve",
        "--village", str(village_dir),
        "--out", str(pred_path),
        "--manifest", str(manifest_path),
        "--preset", preset,
    ]
    if include_text is not None:
        cmd.append("--include-flagged")

    start = datetime.now(timezone.utc)
    try:
        run_cmd(cmd, cwd=PROJECT_ROOT)
        counts = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            counts = manifest.get("counts", {})

        score_payload = None
        truth_path = find_truth_path(village_dir)
        if run_score and truth_path is not None:
            run_cmd(
                [
                    *CLI,
                    "score",
                    "--predictions", str(pred_path),
                    "--truth", str(truth_path),
                    "--out", str(score_path),
                ],
                cwd=PROJECT_ROOT,
            )
            score_payload = json.loads(score_path.read_text(encoding="utf-8"))

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        return CaseResult(
            village=village_dir.name,
            input_path=str(village_dir / "input.geojson"),
            output_dir=str(out_dir),
            status="ok",
            elapsed_seconds=round(elapsed, 4),
            counts=counts,
            score=score_payload,
            error=None,
        )
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        return CaseResult(
            village=village_dir.name,
            input_path=str(village_dir / "input.geojson"),
            output_dir=str(out_dir),
            status="failed",
            elapsed_seconds=round(elapsed, 4),
            counts={},
            score=None,
            error=str(exc),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run full reproducible BhuMe workflow.")
    parser.add_argument("--preset", default="golden", help="Solver preset.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="Directory containing dataset folders.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=DEFAULT_OUT_ROOT,
        help="Directory where workflow artifacts are written.",
    )
    parser.add_argument(
        "--village",
        action="append",
        help="Subset of village folder names/slugs to run. Repeatable.",
    )
    parser.add_argument(
        "--run-score",
        action="store_true",
        help="Run score when a truth file is available.",
    )
    parser.add_argument(
        "--include-flagged",
        action="store_true",
        help="Include explicitly flagged plots in output.",
    )
    parser.add_argument(
        "--base-url",
        default="https://hiring.bhume.in",
        help="Assignment base URL for audit.",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip Playwright page checks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary.",
    )
    return parser


def summarize_case_status(cases: List[CaseResult]) -> Dict[str, Any]:
    if not cases:
        return {"status": "no_cases", "pass": False}
    failed = [c for c in cases if c.status != "ok"]
    return {
        "status": "pass" if not failed else "partial_failure",
        "cases_total": len(cases),
        "cases_ok": len(cases) - len(failed),
        "cases_failed": len(failed),
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    cases = discover_case_dirs(args.data_root, args.village)
    out_root = args.out_root
    out_root.mkdir(parents=True, exist_ok=True)

    audit = None
    if not args.no_audit:
        audit = run_assignment_audit(base_url=args.base_url, project_root=PROJECT_ROOT)
        if not audit.get("allOk"):
            raise RuntimeError(
                f"Assignment page audit failed ({audit.get('routesChecked', 0)} routes checked)."
            )

    results = [run_case(case, preset=args.preset, output_root=out_root, run_score=args.run_score, include_flagged=args.include_flagged) for case in cases]

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "preset": args.preset,
        "base_url": args.base_url,
        "data_root": str(args.data_root),
        "out_root": str(out_root),
        "village_count": len(cases),
        "audit": audit,
        "cases": [case.__dict__ for case in results],
    }
    payload.update(summarize_case_status(results))

    failed = any(item.status != "ok" for item in results)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 1 if failed else 0

    for case in results:
        if case.error is not None:
            print(f"{case.village}: {case.status} in {case.elapsed_seconds}s - {case.error}")
            continue
        print(
            f"{case.village}: {case.status} in {case.elapsed_seconds}s "
            f"corrected={case.counts.get('corrected', 'n/a')} "
            f"flagged={case.counts.get('flagged', 'n/a')}"
        )
        if case.score is not None:
            overlap = case.score.get("overlap_count", 0)
            mean_iou = case.score.get("mean_iou", 0.0)
            mean_delta = case.score.get("mean_area_delta", 0.0)
            print(f"  score: overlap={overlap} iou={mean_iou:.3f} mean_area_delta={mean_delta:.4f}")
    print(
        f"summary: {payload['cases_ok']} ok, {payload['cases_failed']} failed, status={payload['status']}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
