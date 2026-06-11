# BhuMe Boundary Assignment

This repository contains a reproducible Python pipeline for the BhuMe boundary correction take-home assignment. It downloads the assignment data, generates corrected or flagged plot boundary predictions, validates the output schema, and optionally scores predictions against the provided example truths.

## What Is Included

- `src/bhume/`: pipeline source code.
- `scripts/run_workflow.py`: one-command workflow for audit, solve, and scoring.
- `scripts/preset_sweep.py`: compares solver presets across villages.
- `tools/audit_assignment_site.mjs`: Playwright audit for the assignment website/routes.
- `data/`: downloaded assignment inputs, starter kit archive, generated outputs, validation runs, preset sweeps, final predictions, scores, and manifests.
- `transcripts/`: placeholder for chat logs and reasoning snapshots requested by the assignment.

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

Each prediction feature includes:

- `plot_number`
- `status`: `corrected` or `flagged`
- `confidence`: numeric value from `0` to `1`
- `method_note`: short explanation of the solver decision
- `geometry`: resulting plot geometry

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
