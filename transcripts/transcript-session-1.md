# Codex Review Session 1

## User Prompt

Review the BhuMe boundary assignment against the live hiring website using a maximum-effort and devil's-advocate posture.

## Actions Taken

- Opened `https://hiring.bhume.in/`, `/understand/`, `/task/`, `/start/`, `/test/`, and `/submit/`.
- Compared the repository against the assignment contract: runnable code, predictions for attempted villages, transcript evidence, and a video walkthrough.
- Inspected the Python solver, CLI, scorer, validation helper, manifests, final predictions, and transcript folder.
- Ran local scoring through an isolated `uv` environment because the ambient Python environment did not include `numpy` or `geopandas`.

## Findings

- The repository has a real reproducible pipeline and valid final artifacts for both villages.
- Public example truths are all present in the final predictions, but every public truth overlap is flagged.
- The solver originally accepted `boundaries.tif` but did not use it.
- Transcript evidence needed to be replaced with concrete working notes.
- The video still needs to be recorded and supplied through the submission form.

## Changes Directed By The Review

- Use `boundaries.tif` as a secondary hint in the edge-confidence pipeline.
- Make the README explicit about the current scores, conservative correction policy, and confidence interpretation.
- Replace generic transcript instructions with actual working notes and submission evidence.
- Add a local contract checker for prediction files, manifests, transcript presence, and documentation claims.
