"""Command-line interface for the BhuMe assignment workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import DEFAULT_PRESET, SolverConfig
from .download import fetch_all
from .io_utils import read_geojson
from .scorer import score_predictions
from .solver import BoundarySolver
from .validate import maybe_resolve_plot_id_field, validate_predictions_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BhuMe boundary correction toolkit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch = sub.add_parser("fetch", help="Download task assets")
    fetch.add_argument("--out", type=Path, default=Path("data/raw"))

    solve = sub.add_parser("solve", help="Generate predictions")
    solve.add_argument("--village", type=Path, required=False, default=None)
    solve.add_argument("--out", type=Path, required=False, default=None)
    solve.add_argument("--manifest", type=Path, default=None)
    solve.add_argument("--include-flagged", action="store_true")
    solve.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Override preset minimum confidence (0-1). If omitted, preset value is used.",
    )
    solve.add_argument(
        "--preset",
        type=str,
        default=DEFAULT_PRESET,
        choices=SolverConfig.preset_names(),
        help="Preset profile to apply before overrides.",
    )
    solve.add_argument("--config", type=Path, default=None)
    solve.add_argument("--strict", action="store_true")
    solve.add_argument("--skip-validation", action="store_true", help="Skip post-run prediction schema checks.")
    solve.add_argument("--list-presets", action="store_true")

    score = sub.add_parser("score", help="Score predictions against truth")
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--truth", type=Path, required=True)
    score.add_argument("--out", type=Path, default=None)

    return parser.parse_args()


def _load_solver_config(village_dir: Path, args: argparse.Namespace) -> SolverConfig:
    if args.config is not None:
        if not args.config.exists():
            raise FileNotFoundError(f"Config file missing: {args.config}")
        if not args.config.is_file():
            raise ValueError(f"Config path must be a file: {args.config}")
    config_path = village_dir / "solver_config.json"
    user_config: dict[str, Any] = {}
    if args.config is not None:
        config_path = args.config
    if config_path.exists():
        user_config = json.loads(config_path.read_text(encoding="utf-8"))

    min_confidence = float(args.min_confidence) if args.min_confidence is not None else None
    user_override = {}
    if min_confidence is not None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min-confidence must be between 0 and 1.")
        user_override["min_confidence"] = min_confidence
    if args.cmd == "solve":
        if args.include_flagged or "include_flagged" in user_config:
            user_override["include_flagged"] = (
                bool(args.include_flagged) if args.include_flagged else user_config.get("include_flagged", True)
            )
        user_override["strict"] = bool(args.strict) if args.strict else user_config.get("strict", False)
        user_override["preset"] = args.preset

    input_geojson = village_dir / "input.geojson"
    if input_geojson.exists():
        source = read_geojson(input_geojson)
        user_override["plot_id_field"] = maybe_resolve_plot_id_field(
            list(source.columns),
            user_config.get("plot_id_field"),
        )

    return SolverConfig.from_dict(
        {
            **user_config,
            **user_override,
        },
        village_name=village_dir.name,
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    fetch_all(args.out)
    print(f"Downloaded assignment assets to: {args.out}")
    return 0


def cmd_solve(args: argparse.Namespace) -> int:
    if args.list_presets:
        presets = ", ".join(SolverConfig.preset_names())
        print(f"Available presets: {presets}")
        return 0
    if args.village is None:
        raise ValueError("village is required unless --list-presets is used.")
    if args.out is None:
        raise ValueError("out is required unless --list-presets is used.")

    conf = _load_solver_config(args.village, args)
    solver = BoundarySolver(conf)
    imagery = args.village / "imagery.tif"
    boundaries = args.village / "boundaries.tif"
    artifacts = solver.run(
        input_geojson=args.village / "input.geojson",
        output_geojson=args.out,
        imagery=imagery,
        boundaries=boundaries,
        manifest_path=args.manifest,
        include_flagged=conf.include_flagged,
    )

    print(f"wrote: {artifacts.output_path}")
    print(f"manifest: {artifacts.manifest_path}")
    print(
        f"total={artifacts.total}, corrected={artifacts.corrected}, "
        f"flagged={artifacts.flagged}, skipped={artifacts.skipped}"
    )

    if not args.skip_validation:
        validation = validate_predictions_file(artifacts.output_path, plot_id_field=conf.plot_id_field)
        if not validation["valid"] and conf.strict:
            raise ValueError(
                f"Validation failed in strict mode. geometry_null={validation['geometry']['null']} "
                f"geometry_invalid={validation['geometry']['invalid']}"
            )
        print(
            f"validated total={validation['total']} corrected={validation['corrected']} "
            f"flagged={validation['flagged']} ratio_corrected={validation['corrected_ratio']:.4f}"
        )
        if not validation["valid"]:
            print(
                "warning: geometry hygiene issues detected (non-fatal, strict mode not enabled): "
                f"null={validation['geometry']['null']} invalid={validation['geometry']['invalid']}"
            )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    metrics = score_predictions(args.predictions, args.truth, args.out)
    for key, value in metrics.items():
        print(f"{key}: {value}")
    return 0


def main() -> int:
    args = parse_args()
    if args.cmd == "fetch":
        return cmd_fetch(args)
    if args.cmd == "solve":
        return cmd_solve(args)
    if args.cmd == "score":
        return cmd_score(args)
    raise RuntimeError(f"Unsupported command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
