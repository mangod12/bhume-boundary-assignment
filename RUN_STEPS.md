# Run Steps

This is separate from the video script. Use it when recording the walkthrough or when reproducing the project from a fresh checkout.

## A. Quick Verification Of The Checked-In Submission

Run these from the repository root:

```powershell
cd D:\bhume-boundary-assignment
git status --short --branch
python tools\check_submission_contract.py
python -m compileall src scripts tools
```

Expected results:

```text
## main...origin/main
PASS: local submission contract checks passed
compileall completes without errors
```

Check the final prediction files:

```powershell
Get-Item data\outputs\final\vadner\predictions.geojson
Get-Item data\outputs\final\malatavadi\predictions.geojson
```

Upload these two files to the website tester:

```text
https://hiring.bhume.in/test/
```

Use the matching village tab before each upload:

```text
Vadnerbhairav tab -> data\outputs\final\vadner\predictions.geojson
Malatavadi tab -> data\outputs\final\malatavadi\predictions.geojson
```

Expected public tester results:

```text
Vadnerbhairav: 4 corrected, 2 flagged, median IoU 0.888, improvement +0.233, accurate 100%
Malatavadi: 1 corrected, 2 flagged, median IoU 0.763, improvement +0.254, accurate 100%
```

## B. Reproduce From A Fresh Clone

Clone and enter the repo:

```powershell
git clone https://github.com/mangod12/bhume-boundary-assignment.git
cd bhume-boundary-assignment
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies and the local package:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

Confirm the CLI resolves:

```powershell
python -m bhume.cli --help
python -m bhume.cli solve --list-presets
```

Download the assignment data into `data/raw`:

```powershell
python -m bhume.cli fetch --out data/raw
```

Run the full solver workflow:

```powershell
python scripts\run_workflow.py --preset golden --include-flagged --run-score --json --out-root data\outputs\workflow
```

The output folders will be:

```text
data\outputs\workflow\34855_vadnerbhairav_chandavad_nashik
data\outputs\workflow\12429_malatavadi_chandgad_kolhapur
```

Each folder should contain:

```text
predictions.geojson
manifest.json
score.json
```

## C. Optional Website Route Audit

The website audit is optional and separate from the solver. Install Playwright first:

```powershell
npm install --no-save playwright
node tools\audit_assignment_site.mjs > tools\last_audit.json
```

`tools\last_audit.json` is ignored by Git.

You can also run the optional audit through the workflow runner:

```powershell
python scripts\run_workflow.py --audit --preset golden --include-flagged --run-score --json --out-root data\outputs\workflow
```

## D. Final Submission Checklist

Before submitting:

```powershell
python tools\check_submission_contract.py
python -m compileall src scripts tools
git status --short --branch
```

Submit through:

```text
https://hiring.bhume.in/submit/
```

The Google Form asks for:

```text
Name
Email
Phone
GitHub repo URL
5-minute video link
Resume upload
```

The video link must open without a reviewer needing a private login.
