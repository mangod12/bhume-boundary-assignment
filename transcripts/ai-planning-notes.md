# AI Planning Notes

## Problem Framing

The assignment is not asking for hand-fixed parcels. It asks for a method that reads a village bundle, corrects plots only when the evidence is strong enough, flags uncertain cases, and emits calibrated confidence.

## Strategy

- Treat satellite imagery as the primary signal.
- Treat `boundaries.tif` as a rough hint, not truth.
- Prefer conservative correction coverage over high-confidence wrong moves.
- Preserve every plot in the output with either `corrected` or `flagged` status.
- Keep `method_note` short but explicit enough for review.

## Known Limits

- Public example truths are too small to prove calibration.
- The current confidence values are heuristic evidence scores, not learned probabilities.
- The solver mostly handles placement drift; area/record mismatch is flagged rather than reshaped.
- Dense parcels near buildings and canopy remain hard because local edge evidence is ambiguous.

## Video Talking Points

- Show the contract: input bundle to `predictions.geojson`.
- Explain the correction rule and why many plots are flagged.
- Show one corrected plot with imagery plus boundary-hint support.
- Show one flagged plot where the signal is ambiguous or the estimated shift is too large.
- Explain what would come next: better calibration set, boundary-hint ablation, and per-village adaptive thresholds.
