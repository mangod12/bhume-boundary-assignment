# Reconstructed AI Collaboration Log

This is a reconstructed summary of my AI-assisted workflow while working on the BhuMe boundary assignment over roughly two days. It is not a verbatim raw chat export. It documents the actual workflow, decisions, debugging steps, and validation that happened during the assignment.

## 1. Understanding the assignment

I first focused on understanding what BhuMe was asking for, instead of jumping directly into code.

The assignment looked simple at first: read `input.geojson`, inspect satellite imagery, and output `predictions.geojson`. After going through the hiring website, it became clear that the actual task was not just to move polygons. The important part was to decide when a boundary can be corrected and when it should be flagged.

I used the assistant to inspect and summarize the BhuMe assignment website, then I checked the main workflow pages:

- `/understand/`
- `/playground/`
- `/task/`
- `/start/`
- `/test/`
- `/submit/`

From that, I identified the main constraints:

- The input is cadastral plot geometry in `input.geojson`.
- Satellite imagery is the primary signal.
- `boundaries.tif` is available, but it is rough and should not be trusted blindly.
- The output must be a `predictions.geojson` file.
- Every output feature must include `plot_number`, `status`, `confidence`, `method_note`, and geometry.
- `status` must be either `corrected` or `flagged`.
- The score depends on accuracy, confidence calibration, and restraint.
- BhuMe explicitly values knowing when not to correct a plot.

This changed the direction of the project. I stopped thinking of the task as "maximize the number of corrected plots" and started treating it as "make corrections only when evidence is strong enough."

## 2. Checking the starter kit and expected workflow

After understanding the website, I inspected the starter kit and compared it with my repository, using the assistant to speed up the review.

The important takeaway from the starter kit was that the public examples are only a directional check. They are not the full grading set. That meant overfitting to the few public truths would be risky.

I checked the starter kit contract and scorer behavior. The assignment expected a method that can run reproducibly, not hand-edited GeoJSON. This influenced the structure of the repo:

- keep a CLI-based solver;
- generate predictions from input files;
- include manifests and scores;
- include final predictions, manifests, and scores;
- document what the method does and where it fails.

## 3. First audit of the solution

I then audited my existing assignment against the website requirements.

The first serious issue was that the repo was structurally correct but too conservative. It produced valid prediction files, but on the public examples all plots were flagged. That meant the website could not show any positive correction score.

The early result was:

- Vadnerbhairav: all public examples flagged;
- Malatavadi: all public examples flagged;
- no public proof that corrected plots improved over the official position;
- weak confidence-calibration evidence because there were no corrected public examples.

This made the project look compliant but not strong.

## 4. Brainstorming possible methods

I brainstormed what kind of methods would make the solution stronger without overcomplicating it.

The options considered were:

- using a median shift from public truths;
- using an LLM/API in the solver;
- using classical image processing;
- using `boundaries.tif` as a supporting signal;
- using local candidate-grid alignment;
- using confidence gates to avoid unsafe movement.

I rejected some approaches.

Using the public truth median shift would have improved the visible score, but it would be overfitting. It would not be a defensible hidden-set method.

Using an LLM API was also rejected. The assignment says AI-assisted, but that means they want to see how I used AI while building the solution. It does not mean the boundary solver should call an LLM. A remote API would make the project less reproducible and less appropriate for geospatial correction.

The chosen direction was classical, reproducible, and explainable:

- use imagery as the main signal;
- use `boundaries.tif` as a rough hint;
- search for candidate shifts around each plot;
- compare candidate alignment against the official geometry;
- correct only when the candidate clearly beats the official position;
- flag uncertain plots.

## 5. Debugging the website upload

I tested the generated outputs on the actual BhuMe `/test/` page using Playwright.

This found an important bug that local Python tools did not catch: the browser rejected one generated GeoJSON file because it contained raw `NaN` values in properties copied from the source data.

The fix was to make the GeoJSON writer strict:

- convert NumPy scalar values into normal Python values;
- convert `NaN` and infinity values to `null`;
- write JSON with `allow_nan=False`.

After regenerating the outputs, both files passed strict JSON parsing and were accepted by the BhuMe website.

This was a useful example of why testing only locally was not enough. The actual website parser was stricter than the local geospatial stack.

## 6. Improving the correction method

Once the output format was reliable, I worked on improving the actual correction quality.

The first solver version used local edge evidence, but it still failed to show enough public corrections. I then investigated why `boundaries.tif` was not helping enough.

The key discovery was that `boundaries.tif` and `imagery.tif` did not have the same raster grid. The solver was treating them as if they lined up directly, which weakened the boundary-hint signal.

The fix was to resample and align `boundaries.tif` onto the imagery grid before using it.

After that, the method became:

1. Load `input.geojson`, `imagery.tif`, and `boundaries.tif`.
2. Align `boundaries.tif` to the imagery raster grid.
3. For each plot, rasterize the official plot boundary into a local patch.
4. Build local edge evidence from imagery.
5. Blend boundary hints at controlled weight.
6. Search nearby candidate shifts.
7. Compare the best candidate against the official position.
8. Assign confidence based on evidence margin and ambiguity.
9. Correct only when the evidence is strong enough.
10. Otherwise flag the plot.

This improved the result without using public-truth coordinates as a shortcut.

## 7. Confidence and restraint tuning

After the boundary-grid alignment fix, I tuned the gating logic.

One issue was that some good corrections were being rejected because the top candidate and the second-best candidate were very close. After inspection, this often happened because several neighboring pixel shifts described almost the same boundary alignment. So the solver was rejecting some valid plateau cases.

The confidence rule was updated to handle this more carefully:

- a tiny runner-up gap is unsafe if the overall improvement is weak;
- if the candidate clearly beats the official position, a small runner-up gap can still be acceptable;
- if the official boundary has weak support and the candidate is near the threshold, the solver can correct;
- low-evidence candidates remain flagged.

This improved correction coverage without switching to an aggressive mode that moved too many plots.

I also tested an aggressive preset. It improved some public examples but corrected too many plots overall and worsened at least one Malatavadi example. I rejected that version because hidden-set restraint matters.

## 8. Final validation against the live website

After regenerating final outputs, I ran both local checks and the real BhuMe website workflow again.

The final local artifacts passed:

- prediction schema checks;
- strict JSON parsing;
- manifest consistency;
- score file checks;
- review-panel checks;
- website audit JSON checks.

Then the final files were uploaded to the actual `/test/` page using Playwright, with the correct village tab selected before each upload.

Final public website result:

### Vadnerbhairav

- 4 corrected;
- 2 flagged;
- median IoU: 0.888;
- official median IoU: 0.612;
- improvement: +0.233;
- 100% of corrected public plots improved.

### Malatavadi

- 1 corrected;
- 2 flagged;
- median IoU: 0.763;
- official median IoU: 0.510;
- improvement: +0.254;
- 100% of corrected public plots improved.

This confirmed that the final files are accepted by the real BhuMe test portal and that the corrected public plots improve over the official position.

## 9. Final repository updates

I then updated the repository so the submission is explainable and reproducible.

The README was updated with:

- the method;
- final evaluation snapshot;
- public score interpretation;
- signal ablation results;
- useful commands;
- final deliverables;
- submission notes.

Additional artifacts were generated:

- final predictions;
- manifests;
- scores;
- the June 11 raw Codex session log under `transcripts/raw/`;
- the SHA-256 checksum for the primary raw session log.

At the time this note was written, these transcript and integrity files still needed to be committed and pushed before the GitHub repository URL could serve as the final submission evidence.

## 10. What I learned

This assignment taught me that the hard part is not just detecting a shift. The hard part is deciding when the evidence is strong enough to move a legally meaningful boundary.

The most important lesson was that confidence must mean something. If the solver is not sure, the right output is not a random correction with low confidence. The right output is often `flagged`.

The second lesson was that provided data layers need to be checked carefully. `boundaries.tif` was useful, but only after it was aligned to the imagery grid. Treating it as perfectly aligned would have produced weak or misleading results.

The third lesson was that public examples are useful for debugging, but they should not become the method. The final solver improves public examples, but the method is still based on imagery and boundary evidence, not public-truth overfitting.

## 11. How AI assistance was used

AI assistance was useful as a technical pair-programming and review tool in these ways:

- understanding the assignment website;
- extracting exact output requirements;
- comparing the repo against the expected contract;
- proposing possible geospatial correction methods;
- rejecting risky shortcuts like public-truth overfitting;
- debugging browser-side GeoJSON parsing;
- drafting implementation changes for boundary-raster alignment;
- helping review confidence-gate tradeoffs;
- helping create validation scripts;
- running Playwright checks against the real website;
- summarizing remaining risks.

I used AI mainly to accelerate reasoning, implementation, and verification. I made the main design decisions: prefer reproducibility, avoid public-truth overfitting, reject a runtime LLM dependency, and keep uncertain plots flagged.
