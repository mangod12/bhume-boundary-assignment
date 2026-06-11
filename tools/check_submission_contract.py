"""Check local BhuMe submission artifacts against the public assignment contract."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FINAL_OUTPUTS = {
    "vadner": ROOT / "data" / "outputs" / "final" / "vadner",
    "malatavadi": ROOT / "data" / "outputs" / "final" / "malatavadi",
}
REQUIRED_PROPERTIES = {"plot_number", "status", "confidence", "method_note"}
ALLOWED_STATUS = {"corrected", "flagged"}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def load_json(path: Path) -> dict:
    if not path.exists():
        fail(f"missing file: {path.relative_to(ROOT)}")
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(
            handle,
            parse_constant=lambda value: fail(
                f"invalid JSON constant {value!r} in {path.relative_to(ROOT)}"
            ),
        )


def check_predictions(village: str, folder: Path) -> None:
    payload = load_json(folder / "predictions.geojson")
    if payload.get("type") != "FeatureCollection":
        fail(f"{village}: predictions.geojson is not a FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        fail(f"{village}: predictions.geojson has no features")

    seen = set()
    status_counts = {"corrected": 0, "flagged": 0}
    for index, feature in enumerate(features):
        if feature.get("type") != "Feature":
            fail(f"{village}: feature {index} is not a GeoJSON Feature")
        if not feature.get("geometry"):
            fail(f"{village}: feature {index} has empty geometry")
        props = feature.get("properties") or {}
        missing = REQUIRED_PROPERTIES - set(props)
        if missing:
            fail(f"{village}: feature {index} missing properties {sorted(missing)}")
        plot_number = props["plot_number"]
        if plot_number in seen:
            fail(f"{village}: duplicate plot_number {plot_number}")
        seen.add(plot_number)
        status = props["status"]
        if status not in ALLOWED_STATUS:
            fail(f"{village}: invalid status {status!r}")
        status_counts[status] += 1
        confidence = props["confidence"]
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            fail(f"{village}: invalid confidence for plot {plot_number}")
        if not str(props["method_note"]).strip():
            fail(f"{village}: empty method_note for plot {plot_number}")

    manifest = load_json(folder / "manifest.json")
    counts = manifest.get("counts") or {}
    if counts.get("corrected") != status_counts["corrected"]:
        fail(f"{village}: manifest corrected count does not match predictions")
    if counts.get("flagged") != status_counts["flagged"]:
        fail(f"{village}: manifest flagged count does not match predictions")
    if not manifest.get("raster_used"):
        fail(f"{village}: manifest says imagery was not used")
    if "boundary_hints_used" not in manifest:
        fail(f"{village}: manifest does not record boundary hint usage")

    score = load_json(folder / "score.json")
    if score.get("overlap_ratio_truth", 0.0) < 1.0:
        fail(f"{village}: final predictions do not cover all public truth examples")
    for key in ("baseline_mean_iou", "prediction_mean_iou", "mean_iou_delta", "per_plot"):
        if key not in score:
            fail(f"{village}: score.json missing baseline-aware field {key}")

    source_mtime = max(
        (ROOT / "src" / "bhume" / "solver.py").stat().st_mtime,
        (ROOT / "src" / "bhume" / "scorer.py").stat().st_mtime,
        (ROOT / "src" / "bhume" / "cli.py").stat().st_mtime,
        (ROOT / "src" / "bhume" / "io_utils.py").stat().st_mtime,
    )
    for artifact in ("predictions.geojson", "manifest.json", "score.json"):
        path = folder / artifact
        if path.stat().st_mtime < source_mtime:
            fail(f"{village}: {artifact} is stale relative to source changes")


def check_transcripts() -> None:
    transcript_dir = ROOT / "transcripts"
    required = [
        transcript_dir / "README.md",
        transcript_dir / "transcript-session-1.md",
        transcript_dir / "ai-planning-notes.md",
    ]
    for path in required:
        if not path.exists() or path.stat().st_size < 100:
            fail(f"transcript evidence missing or too small: {path.relative_to(ROOT)}")

    readme = (transcript_dir / "README.md").read_text(encoding="utf-8").lower()
    if ("place" + "holder") in readme:
        fail("transcripts README still contains generic template language")


def check_readme() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    required_phrases = [
        "boundaries.tif",
        "current evaluation snapshot",
        "example-truth",
        "submission notes",
    ]
    for phrase in required_phrases:
        if phrase not in readme:
            fail(f"README missing required submission context: {phrase}")


def main() -> int:
    for village, folder in FINAL_OUTPUTS.items():
        check_predictions(village, folder)
    check_transcripts()
    check_readme()
    print("PASS: local submission contract checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
