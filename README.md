# BhuMe Boundary Assignment

This repository contains a reproducible Python pipeline for the BhuMe boundary correction take-home assignment. It downloads the assignment data, generates corrected or flagged plot boundary predictions, validates the output schema, and optionally scores predictions against the provided example truths.

## What Is Included

- `src/bhume/`: pipeline source code.
- `scripts/run_workflow.py`: one-command workflow for audit, solve, and scoring.
- `scripts/preset_sweep.py`: compares solver presets across villages.
- `tools/audit_assignment_site.mjs`: Playwright audit for the assignment website/routes.
- `data/`: downloaded assignment inputs, starter kit archive, generated outputs, validation runs, preset sweeps, final predictions, scores, and manifests.
- `transcripts/`: AI-use evidence, review notes, and reasoning snapshots requested by the assignment.

The repository includes the local assignment data and generated artifacts present in this workspace. They can also be regenerated with the commands below.

## Install

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
pip install -r requirements.txt
pip install -e .
```

Python 3.10 or newer is required.

## How The Pipeline Works

1. `fetch` downloads the village inputs and starter kit into `data/raw`.
2. `solve` reads each village folder, applies a conservative correction preset, writes `predictions.geojson`, and records a run manifest.
3. Output validation checks required fields, allowed statuses, confidence bounds, and invalid geometry counts.
4. `score` compares predictions to `example_truths.geojson` when example truths are present.
5. The final artifacts are stored under `data/outputs/final/<village>/`.

The solver uses satellite imagery as the primary signal and `boundaries.tif` as a secondary hint. The hint layer is first aligned onto the imagery grid, then blended at controlled weight because the assignment describes it as rough: it can support an edge decision, but it is not treated as ground truth.

Each prediction feature includes:

- `plot_number`
- `status`: `corrected` or `flagged`
- `confidence`: numeric value from `0` to `1`
- `method_note`: short explanation of the solver decision
- `geometry`: resulting plot geometry

## Current Evaluation Snapshot

The public example truths are tiny samples, so I use them as schema and sanity checks rather than as a complete leaderboard. The final preset corrects plots only when local imagery and the aligned boundary hint beat the official geometry by a calibrated margin; otherwise it flags the plot to protect precision and calibration.

| Village | Plots | Corrected | Flagged | Example-truth overlap | Baseline IoU | Prediction IoU | Mean IoU delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vadnerbhairav | 2,457 | 923 | 1,534 | 6/6 | 0.5988 | 0.7464 | +0.1475 |
| Malatavadi | 2,508 | 231 | 2,277 | 3/3 | 0.4301 | 0.5146 | +0.0845 |

On the public example truths, the final output improves corrected plots without a public-example regression: Vadnerbhairav has 4 corrected and 2 flagged examples; Malatavadi has 1 corrected and 2 flagged examples. The video walkthrough should still stress that these are tiny public samples and that the hidden set is the real target.

Confidence is an evidence score, not a learned probability. Higher values require the best candidate shift to beat the official baseline by a clear score margin, with special handling for one-pixel candidate plateaus. Flagged plots keep low confidence unless they are rejected for geometry or area mismatch.

## Signal Ablation Snapshot

The ablation report under `data/outputs/ablation/summary.json` runs on the public-truth subset. It is intentionally not used to tune the final thresholds; it explains why the final combined mode is conservative.

| Signal mode | Vadner public delta | Malatavadi public delta | Interpretation |
| --- | ---: | ---: | --- |
| imagery only | 0.0000 | 0.0000 | Imagery alone is too weak on the public subset. |
| boundaries only | +0.2350 | -0.0799 | Strong on Vadnerbhairav but harmful on Malatavadi. |
| imagery + boundaries | +0.1475 | +0.0845 | Final mode: improves both public subsets while preserving flags. |

## Reproduce From Scratch

Download assignment data:

```powershell
python -m bhume.cli fetch --out data/raw
```

Run the full workflow for both villages using the final preset:

```powershell
python scripts/run_workflow.py --preset golden --run-score --json --out-root data/outputs/workflow
```

Generate final outputs manually for one village:

```powershell
python -m bhume.cli solve `
  --village data/raw/34855_vadnerbhairav_chandavad_nashik `
  --out data/outputs/final/vadner/predictions.geojson `
  --manifest data/outputs/final/vadner/manifest.json `
  --preset golden `
  --include-flagged
```

Score one output:

```powershell
python -m bhume.cli score `
  --predictions data/outputs/final/vadner/predictions.geojson `
  --truth data/raw/34855_vadnerbhairav_chandavad_nashik/example_truths.geojson `
  --out data/outputs/final/vadner/score.json
```

## Useful Commands

List CLI commands:

```powershell
python -m bhume.cli --help
```

List solver presets:

```powershell
python -m bhume.cli solve --list-presets
```

Run a preset comparison:

```powershell
python scripts/preset_sweep.py --presets conservative balanced aggressive golden
```

Run the assignment website audit:

```powershell
node tools/audit_assignment_site.mjs > tools/last_audit.json
```

Check local submission constraints:

```powershell
python tools/check_submission_contract.py
```

Run the ablation report:

```powershell
python tools/run_ablation_report.py --preset golden
```

Render visual review panels:

```powershell
python tools/render_review_panels.py `
  --village data/raw/34855_vadnerbhairav_chandavad_nashik `
  --predictions data/outputs/final/vadner/predictions.geojson `
  --truth data/raw/34855_vadnerbhairav_chandavad_nashik/example_truths.geojson `
  --out-dir data/outputs/review-panels/vadner
```

## Data And Deliverables

The checked-in final outputs are:

- `data/outputs/final/vadner/predictions.geojson`
- `data/outputs/final/vadner/manifest.json`
- `data/outputs/final/vadner/score.json`
- `data/outputs/final/malatavadi/predictions.geojson`
- `data/outputs/final/malatavadi/manifest.json`
- `data/outputs/final/malatavadi/score.json`

These are the assignment-ready artifacts. The rest of the repository exists to explain and reproduce how they were generated.

Additional checked-in data includes:

- raw downloaded assignment folders under `data/raw/`
- unpacked village folders under `data/`
- preset sweep and validation outputs under `data/outputs/`
- assignment starter archive and download manifests

## Submission Notes

- AI-use evidence is under `transcripts/`.
- The 5-minute video is not stored in Git; submit the hosted link in the Google Form.
- The video should focus on the tradeoff between correction coverage and trust: why the solver leaves many plots flagged, what evidence makes a correction acceptable, where the method breaks, and how `boundaries.tif` is used only as a hint.
