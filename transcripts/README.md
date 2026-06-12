# AI Transcript And Workflow Index

This folder contains the AI-use evidence requested by the BhuMe assignment.

- `raw/rollout-2026-06-11T11-39-58-019eb54d-3b30-7c82-82f7-79570ef5ea7b.jsonl`: unmodified Codex raw session log from the first BhuMe assignment work session.
- `raw/SHA256SUMS.txt`: SHA-256 checksum for the unmodified raw session log.
- `raw/raw-transcript-bhume-2026-06-11.jsonl` and `raw/raw-transcript-bhume-2026-06-11.md`: readable transcript export derived from the June 11 raw session log.
- `transcript-session-1.md`: Readable Codex review and verification summary, including the initial gaps, fixes, and final live-site result.
- `ai-planning-notes.md`: Planning and video notes for the correction method, limitations, and final walkthrough story.
- `reconstructed-ai-workflow-log.md`: Reconstructed two-day collaboration summary covering problem understanding, method selection, implementation, debugging, and final validation. This is not a verbatim raw export.

The June 11 `rollout-*.jsonl` file is the primary raw chat transcript. It is copied from the local Codex session history and intentionally keeps the native Codex JSONL structure, including tool events and metadata. `SHA256SUMS.txt` makes that file tamper-evident: if the raw log changes after submission, its SHA-256 hash will no longer match the manifest.

The June 11 `raw-transcript-bhume-*` files are secondary readable exports for convenience. They preserve the chronological chat/action flow in a cleaner format, but they are not the primary raw evidence.

The Markdown notes tell the same workflow in a condensed form: assignment understanding, early conservative output, `NaN` upload fix, boundary-raster alignment, final website validation, and submission preparation. They are context for reviewers, not a substitute for the raw chat transcript exports.

To verify the transcript evidence locally:

```powershell
python tools\check_submission_contract.py
Get-FileHash transcripts\raw\rollout-*.jsonl -Algorithm SHA256
```

The raw log is tamper-evident in this working tree. It becomes history-backed submission evidence once these files are committed and pushed to the GitHub repository used in the form.

If additional web-chat share links are used before submission, list them here with the public share URL.
