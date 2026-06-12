# Five-Minute Video Script

Purpose: record a clear technical walkthrough for the BhuMe submission form. Use this as a guide, not a word-for-word performance. Keep the tone practical. Show the repo, the final predictions, the solver code, and the public tester. Do not present the public tester as the final grade.

## 0:00-0:25 - Problem Framing

Screen: open `README.md`, then the BhuMe task page.

Spoken script:

I treated this as a trust problem, not just a polygon-moving problem. The input gives official parcel polygons, satellite imagery, and rough boundary hints. The output is a `predictions.geojson` file where each plot is either corrected or flagged. My goal was not to move everything. My goal was to move a plot only when local evidence was stronger than the official starting boundary.

## 0:25-0:55 - Submission Contract

Screen: show the task page contract, then one final `predictions.geojson` feature.

Spoken script:

The website contract asks for a FeatureCollection in EPSG:4326. Each feature includes `plot_number`, `status`, `confidence`, `method_note`, and geometry. `status` is either `corrected` or `flagged`. A corrected plot contains the shifted boundary. A flagged plot keeps the original boundary and records that the solver did not find enough evidence for a safe correction.

## 0:55-1:45 - Method Overview

Screen: open `src/bhume/solver.py` and scroll through the main flow.

Spoken script:

The runtime pipeline is deterministic. It does not call an LLM or external API. For each village, it loads `input.geojson`, `imagery.tif`, and `boundaries.tif` when present. For each plot, it creates a local raster window around the official geometry, extracts imagery edge evidence, aligns the rough boundary hint to the imagery grid, and blends those signals carefully.

Then it rasterizes the official parcel boundary and searches candidate x and y shifts around the original polygon. Every candidate is scored against the local evidence. The solver also scores the original unshifted geometry, so a correction must beat the official baseline by a clear margin before it is accepted.

## 1:45-2:35 - Confidence And Restraint

Screen: show `src/bhume/config.py`, manifest counts, and `method_note` examples.

Spoken script:

Confidence is an evidence score. It increases when the best candidate clearly beats the baseline and when the local evidence is not just a flat one-pixel plateau. It stays lower when the decision is ambiguous. This matters because the assignment says confidence calibration is watched heavily.

The solver is intentionally restrained. Vadnerbhairav has 923 corrected and 1534 flagged plots. Malatavadi has 231 corrected and 2277 flagged plots. Malatavadi is more crowded and has smaller parcels, so the solver is more conservative there. That tradeoff gives up some coverage, but it reduces dangerous false corrections.

## 2:35-3:35 - Reproducibility

Screen: show `RUN_STEPS.md`, then `scripts/run_workflow.py`.

Spoken script:

The repo includes the final submission artifacts under `data/outputs/final`. It also includes a reproducible workflow. From a fresh checkout, install the Python dependencies, install the package in editable mode, fetch the assignment data, and run `scripts/run_workflow.py` with the golden preset and `--include-flagged`. The local checker validates the submission contract: required properties, allowed statuses, confidence bounds, manifest counts, transcript evidence, and score file fields.

## 3:35-4:20 - Public Website Results

Screen: open `https://hiring.bhume.in/test/`, select each tab, upload the matching final file.

Spoken script:

On the public tester, I selected the matching village tab before uploading each file. For Vadnerbhairav, the public score is 4 corrected and 2 flagged out of 6 truths, median IoU 0.888, improvement plus 0.233, and 100 percent accurate at IoU 0.5. For Malatavadi, the public score is 1 corrected and 2 flagged out of 3 truths, median IoU 0.763, improvement plus 0.254, and 100 percent accurate.

These public truths are only a small sanity check. I did not use the public truth coordinates to hand tune individual plots.

## 4:20-4:50 - Failure Modes

Screen: show README notes about hidden-set risk.

Spoken script:

The main failure mode is hidden-set restraint. If the rough boundary hint is wrong, a solver that trusts it too much can over-correct. That is why this method always compares against the official baseline and flags ambiguous plots. Another limitation is that confidence is hand-calibrated from evidence margins, not learned from a large labeled dataset. With more time, I would build a larger validation set, tune confidence against real misses, and add more shape-aware checks.

## 4:50-5:00 - Close

Screen: show final repo root and submit page.

Spoken script:

The submission is one GitHub repo with code, predictions, and AI-use notes. The core idea is conservative correction: move a plot only when evidence beats the official boundary, otherwise flag it for review.
