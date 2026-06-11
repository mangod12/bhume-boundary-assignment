# 5-Minute BhuMe Walkthrough Script

Target length: about 5 minutes.

Recording setup:

- Record your screen and microphone.
- Keep the repo open in one window and the BhuMe website in another.
- Speak naturally. Do not read every number mechanically; the goal is to explain your thinking.
- If you run over time, skip the optional lines marked "optional".

## 0:00-0:15

Hi, I am walking through my BhuMe boundary correction submission.

The main thing I want to show is that I treated this as a trust problem, not just a polygon-moving problem.

## 0:15-0:35

The task is to read the official plot boundaries, compare them against satellite imagery and the rough boundary hint layer, and output a `predictions.geojson`.

For every plot, the solver must either return a corrected geometry or flag it when the evidence is not strong enough.

## 0:35-0:55

I started by going through the BhuMe assignment pages: `understand`, `task`, `start`, `test`, and `submit`.

The key scoring dimensions are accuracy, confidence calibration, and restraint. That changed how I approached the problem.

## 0:55-1:15

The public examples are very small: six plots in Vadnerbhairav and three in Malatavadi.

So I used the public tester as a sanity check, not as a training set. I did not hard-code shifts from the public truths.

## 1:15-1:40

The inputs are `input.geojson`, `imagery.tif`, and `boundaries.tif`.

`imagery.tif` is the primary evidence. `boundaries.tif` is useful, but it is rough, so I only use it as a supporting hint.

## 1:40-2:05

One important implementation detail is that `boundaries.tif` and `imagery.tif` are not assumed to share the same raster grid.

The solver first aligns the boundary hint raster onto the imagery grid. Without this step, the hint layer can look weaker or misleading.

## 2:05-2:35

For each plot, I rasterize the official polygon boundary into a local image patch.

Then I score nearby candidate shifts against local imagery edges and the aligned boundary hint layer.

The baseline is always the official plot location, so the candidate has to beat the official geometry, not just look plausible.

## 2:35-3:00

Confidence is based on evidence margin.

If the best candidate clearly beats the official polygon and the ambiguity is acceptable, the plot is marked `corrected`.

If the evidence is weak, noisy, or ambiguous, the plot is marked `flagged`.

## 3:00-3:25

This restraint matters because a bad land-boundary correction is worse than no correction.

I deliberately leave many plots flagged. The solver is designed to be useful when it is confident, not to force every plot into a correction.

## 3:25-3:55

Now I will show the public tester result.

For Vadnerbhairav, the website scores 4 corrected and 2 flagged examples. The corrected examples have median IoU 0.888 compared to official 0.612, with 100 percent of corrected public plots improved.

## 3:55-4:20

For Malatavadi, the website scores 1 corrected and 2 flagged examples.

The corrected example has median IoU 0.763 compared to official 0.510, again with the corrected public plot improved.

## 4:20-4:40

I also included review panels and an ablation summary.

The ablation shows why I did not use the boundary hint alone: it helps one village but can hurt the other. The final mode combines imagery and aligned boundary hints.

## 4:40-5:00

The main failure modes are weak imagery edges, crop boundaries that do not match legal boundaries, and noisy hints.

In those cases, the solver should flag the plot rather than inventing a boundary.

## 5:00-5:20

I used AI as a pair-programming and review assistant.

It helped me understand the assignment contract, compare the repo against the website, debug the browser GeoJSON issue, improve the raster alignment method, and run Playwright checks against the live website.

## 5:20-5:35

I did not use an LLM inside the solver.

The solver itself is deterministic and reproducible from the provided data.

## 5:35-5:50

The final outputs are in `data/outputs/final`, and the repo includes the exact predictions, manifests, scores, review panels, and website audit artifacts.

## 5:50-6:00

That is the submission: a reproducible correction pipeline that improves confident cases, flags uncertain cases, and treats confidence as part of the method rather than just a field in the output.

