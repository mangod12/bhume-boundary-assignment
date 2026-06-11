"""Core correction pipeline for the BhuMe assignment."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .config import (
    STATUS_CORRECTED,
    STATUS_FLAGGED,
    SolverConfig,
    CONFIDENCE_MIN,
    CONFIDENCE_MAX,
)
from .deps import shapely, pyproj
from .geometry import area_and_field, clamp_confidence, detect_area_field
from .io_utils import RasterLoadResult, read_geojson, read_raster, write_predictions

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


@dataclass(frozen=True)
class SolveArtifacts:
    output_path: Path
    manifest_path: Path
    total: int
    corrected: int
    flagged: int
    skipped: int
    elapsed_seconds: float


@dataclass(frozen=True)
class PlotDecision:
    geometry: object
    status: str
    confidence: float
    method_note: str


@dataclass(frozen=True)
class AlignmentPrior:
    dx_px: float
    dy_px: float
    confidence: float
    samples: int
    support: int
    consistency: float
    mean_margin: float


class BoundarySolver:
    def __init__(self, config: SolverConfig):
        self.config = config
        self._gpd = __import__("geopandas")
        self._shapely = shapely()
        self._confidences = []
        self._repair_stats = {"attempted": 0, "repaired": 0, "unrepairable": 0}

    def run(
        self,
        input_geojson: Path,
        output_geojson: Path,
        *,
        imagery: Optional[Path] = None,
        boundaries: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
        include_flagged: Optional[bool] = None,
    ) -> SolveArtifacts:
        include_flagged = self.config.include_flagged if include_flagged is None else include_flagged
        source = read_geojson(input_geojson)

        self._confidences = []
        self._repair_stats = {"attempted": 0, "repaired": 0, "unrepairable": 0}

        raster = read_raster(imagery) if imagery and imagery.exists() else None
        boundary_raster = read_raster(boundaries) if boundaries and boundaries.exists() else None
        boundary_raster = self._align_boundary_raster(boundary_raster, raster)
        source_crs = getattr(source, "crs", None)
        alignment_prior = self._estimate_alignment_prior(
            source=source,
            raster=raster,
            boundary_raster=boundary_raster,
            source_crs=source_crs,
        )

        out_features = []
        counters = {"total": 0, "corrected": 0, "flagged": 0, "skipped": 0}
        start = time.time()

        for _, row in source.iterrows():
            counters["total"] += 1
            decision = self._solve_plot(
                row,
                raster=raster,
                boundary_raster=boundary_raster,
                source_crs=source_crs,
                alignment_prior=alignment_prior,
            )

            normalized = self._normalize_geometry_for_output(decision.geometry)
            if normalized is None:
                counters["skipped"] += 1
                continue

            if normalized is not decision.geometry:
                suffix = " [geometry normalized for validity]"
                method_note = f"{decision.method_note}{suffix}" if decision.method_note else suffix.strip()
                decision = PlotDecision(
                    geometry=normalized,
                    status=decision.status,
                    confidence=decision.confidence,
                    method_note=method_note,
                )

            if decision.status == STATUS_FLAGGED and not include_flagged:
                counters["skipped"] += 1
                continue

            out_features.append(self._build_feature(row, decision))
            self._confidences.append(decision.confidence)
            if decision.status == STATUS_CORRECTED:
                counters["corrected"] += 1
            else:
                counters["flagged"] += 1

        if not out_features:
            raise RuntimeError("No output features were produced; check input data and configuration.")

        write_predictions(output_geojson, out_features)
        elapsed = time.time() - start
        manifest = Path(manifest_path) if manifest_path else output_geojson.with_name("manifest.json")
        artifacts = SolveArtifacts(
            output_path=output_geojson,
            manifest_path=manifest,
            total=counters["total"],
            corrected=counters["corrected"],
            flagged=counters["flagged"],
            skipped=counters["skipped"],
            elapsed_seconds=round(elapsed, 4),
        )
        self._write_manifest(artifacts, bool(raster), bool(boundary_raster), alignment_prior)
        return artifacts

    def _solve_plot(
        self,
        row,
        raster: Optional[RasterLoadResult],
        boundary_raster: Optional[RasterLoadResult],
        source_crs=None,
        alignment_prior: Optional[AlignmentPrior] = None,
    ) -> PlotDecision:
        geometry = getattr(row, "geometry", None)
        if not self._is_valid_geometry(geometry):
            return PlotDecision(
                geometry=geometry,
                status=STATUS_FLAGGED,
                confidence=0.85,
                method_note="Input geometry is invalid or empty; cannot safely correct.",
            )

        area_info = area_and_field(row)
        provided_area = area_info.provided
        properties = row.to_dict() if hasattr(row, "to_dict") else {}
        area_field = detect_area_field(properties)
        if provided_area is not None and area_field and area_field.endswith("_ha"):
            provided_area *= 10000.0
        geom_area = area_info.polygon_sq_m

        if geom_area is not None and provided_area is not None:
            ratio = geom_area / provided_area if provided_area > 0 else 1.0
            if ratio < self.config.area_ratio_low or ratio > self.config.area_ratio_high:
                return PlotDecision(
                    geometry=geometry,
                    status=STATUS_FLAGGED,
                    confidence=0.93,
                    method_note=(
                        "Flagged: strong evidence of area mismatch between geometry "
                        "and recorded area; likely a non-placement-only issue."
                    ),
                )

        if raster is None or raster.array is None:
            return PlotDecision(
                geometry=geometry,
                status=STATUS_FLAGGED,
                confidence=clamp_confidence(max(self.config.min_confidence, 0.2)),
                method_note="Rasters unavailable; flagged for manual review.",
            )

        shifted, shift_note, shift_conf = self._estimate_shift(
            row,
            raster=raster,
            boundary_raster=boundary_raster,
            source_crs=source_crs,
            alignment_prior=alignment_prior,
        )
        if shifted is not None and shift_conf >= self.config.shift_threshold:
            conf = max(self.config.min_confidence, shift_conf)
            return PlotDecision(
                geometry=shifted,
                status=STATUS_CORRECTED,
                confidence=clamp_confidence(conf),
                method_note=shift_note,
            )

        return PlotDecision(
            geometry=geometry,
            status=STATUS_FLAGGED,
            confidence=clamp_confidence(max(self.config.min_confidence, 0.35)),
            method_note=(
                f"No reliable correction signal found ({shift_note}). "
                "Plot flagged to preserve precision."
            ),
        )

    def _estimate_shift(
        self,
        row,
        raster: RasterLoadResult,
        boundary_raster: Optional[RasterLoadResult],
        source_crs=None,
        alignment_prior: Optional[AlignmentPrior] = None,
    ) -> Tuple[Optional[object], str, float]:
        geom = row.geometry
        if not self._is_valid_geometry(geom):
            return None, "invalid input geometry", 0.0

        target_geom = self._transform_geometry(geom, source_crs, raster.crs)
        if not self._is_valid_geometry(target_geom):
            return None, "Geometry could not be transformed into raster CRS.", 0.0

        try:
            arr = raster.array
            if arr.size == 0 or np.isnan(arr).all():
                return None, "Raster unavailable for local shift estimation.", 0.0

            from rasterio.transform import rowcol, xy

            cx, cy = target_geom.centroid.x, target_geom.centroid.y
            r, c = rowcol(raster.transform, cx, cy)
            if r < 0 or c < 0 or r >= raster.height or c >= raster.width:
                return None, "Centroid outside raster extent.", 0.0

            half_window = max(self.config.max_shift_pixels * 4, 16)
            half_window = int(min(half_window, 80))
            r0 = max(0, r - half_window)
            r1 = min(raster.height, r + half_window)
            c0 = max(0, c - half_window)
            c1 = min(raster.width, c + half_window)
            patch = arr[r0:r1, c0:c1]
            if patch.size < 256:
                return None, "Empty or too-small raster patch.", 0.0

            gx = np.nan_to_num(np.gradient(patch)[0], nan=0.0)
            gy = np.nan_to_num(np.gradient(patch)[1], nan=0.0)
            edge_strength = np.abs(gx) + np.abs(gy)
            if np.isnan(edge_strength).all() or edge_strength.size == 0:
                return None, "No informative local raster signal.", 0.0

            boundary_patch = self._boundary_hint_patch(
                boundary_raster=boundary_raster,
                r0=r0,
                r1=r1,
                c0=c0,
                c1=c1,
                shape=edge_strength.shape,
            )
            hint_note = "imagery"
            boundary_signal_score = 0.0
            if boundary_patch is not None:
                edge_strength, boundary_signal_score = self._blend_boundary_hints(
                    edge_strength=edge_strength,
                    boundary_patch=boundary_patch,
                )
                if boundary_signal_score > 0.0:
                    hint_note = "imagery+boundary-hints"

            edge_max = float(edge_strength.max())
            edge_mean = float(edge_strength.mean())
            if edge_max <= 0 or edge_mean <= 0:
                return None, "No informative local raster edge signal.", 0.0

            high_edge_mask = edge_strength >= np.percentile(edge_strength, 99.5)
            if not bool(high_edge_mask.any()):
                return None, "No sharp local edge response.", 0.0

            yy, xx = np.where(high_edge_mask)
            weights = edge_strength[yy, xx]
            if weights.sum() <= 0:
                return None, "Edge mask had zero strength.", 0.0

            yyc = float(np.average(yy, weights=weights))
            cxc = float(np.average(xx, weights=weights))
            dy_px = yyc - patch.shape[0] / 2.0
            dx_px = cxc - patch.shape[1] / 2.0
            blend_note = "centroid"
            phase_confidence = 0.0
            phase_shift_quality = 0.0
            selected_score = 0.0

            template = self._rasterize_geometry_edges(
                geometry=target_geom,
                raster_transform=raster.transform,
                r0=r0,
                r1=r1,
                c0=c0,
                c1=c1,
                shape=edge_strength.shape,
            )
            grid = self._score_candidate_grid(
                edge_patch=edge_strength,
                template=template,
                max_shift_pixels=self.config.max_shift_pixels,
                boundary_signal_score=boundary_signal_score,
            )
            if grid is not None:
                grid["source"] = "local candidate-grid"
            prior_grid = self._score_prior_candidate_grid(
                edge_patch=edge_strength,
                template=template,
                max_shift_pixels=self.config.max_shift_pixels,
                boundary_signal_score=boundary_signal_score,
                alignment_prior=alignment_prior,
            )
            selected_grid = self._select_grid_candidate(grid, prior_grid)
            if selected_grid is not None:
                dx_px = selected_grid["dx_px"]
                dy_px = selected_grid["dy_px"]
                dx_m, dy_m = self._pixels_to_meters(dx_px, dy_px, raster.transform)
                if math.isnan(dx_m) or math.isnan(dy_m) or math.isinf(dx_m) or math.isinf(dy_m):
                    return None, "Invalid grid-search shift in map space.", 0.0

                from shapely.affinity import translate

                corrected = translate(target_geom, xoff=dx_m, yoff=dy_m)
                corrected = self._transform_geometry(corrected, raster.crs, source_crs)
                if not self._is_valid_geometry(corrected):
                    return None, "Grid-search corrected geometry failed final validity checks.", 0.0

                confidence = selected_grid["confidence"]
                note = (
                    f"{selected_grid.get('source', 'candidate-grid')} alignment using local edge evidence ({hint_note}): "
                    f"dx={dx_m:.3f}, dy={dy_m:.3f}, px={dx_px:.0f},{dy_px:.0f}, "
                    f"baseline_score={selected_grid['baseline_score']:.4f}, "
                    f"best_score={selected_grid['best_score']:.4f}, "
                    f"score_margin={selected_grid['score_margin']:.4f}, "
                    f"second_margin={selected_grid['second_margin']:.4f}, "
                    f"conf={confidence:.3f}"
                )
                if alignment_prior is not None:
                    note = (
                        f"{note}, global_prior_px={alignment_prior.dx_px:.1f},{alignment_prior.dy_px:.1f}, "
                        f"global_prior_conf={alignment_prior.confidence:.3f}, "
                        f"global_prior_consistency={alignment_prior.consistency:.3f}"
                    )
                if confidence < self.config.shift_threshold or selected_grid["score_margin"] <= 0.0:
                    return (
                        None,
                        f"Grid-search margin below correction floor ({note}).",
                        confidence,
                    )
                return corrected, note, confidence

            centroid_score = self._edge_alignment_score(
                edge_patch=edge_strength,
                template=template,
                dx_px=float(dx_px),
                dy_px=float(dy_px),
            )
            if template is not None and float(template.sum()) > 0.0:
                phase_shift = self._estimate_shift_by_phase_correlation(
                    edge_patch=edge_strength,
                    template=template,
                )
                if phase_shift is not None:
                    phase_shift_dx = phase_shift["dx_px"]
                    phase_shift_dy = phase_shift["dy_px"]
                    phase_confidence = phase_shift["confidence"]
                    phase_shift_quality = self._edge_alignment_score(
                        edge_patch=edge_strength,
                        template=template,
                        dx_px=float(phase_shift_dx),
                        dy_px=float(phase_shift_dy),
                    )

                    near = (
                        math.fabs(phase_shift_dx - dx_px) <= 2.0
                        and math.fabs(phase_shift_dy - dy_px) <= 2.0
                    )
                    if near and (phase_shift_quality > centroid_score + 0.03) and phase_confidence > 0.22:
                        blend_note = "blended centroid+phase"
                        dx_px = 0.5 * dx_px + 0.5 * phase_shift_dx
                        dy_px = 0.5 * dy_px + 0.5 * phase_shift_dy
                        selected_score = max(phase_shift_quality, centroid_score)
                    elif (
                        phase_shift_quality >= 0.08
                        and phase_confidence >= 0.40
                        and not near
                    ):
                        blend_note = "phase-correlation"
                        dx_px = phase_shift_dx
                        dy_px = phase_shift_dy
                        selected_score = phase_shift_quality
            if (
                math.fabs(dx_px) > self.config.max_shift_pixels
                or math.fabs(dy_px) > self.config.max_shift_pixels
            ):
                return (
                    None,
                    f"Estimated shift too large (dx_px={dx_px:.2f}, dy_px={dy_px:.2f}) relative to conservative policy.",
                    0.0,
                )

            dx_m, dy_m = self._pixels_to_meters(dx_px, dy_px, raster.transform)
            if math.isnan(dx_m) or math.isnan(dy_m) or math.isinf(dx_m) or math.isinf(dy_m):
                return None, "Invalid shift in map space.", 0.0

            from shapely.affinity import translate

            corrected = translate(target_geom, xoff=dx_m, yoff=dy_m)
            corrected = self._transform_geometry(corrected, raster.crs, source_crs)
            if not self._is_valid_geometry(corrected):
                return None, "Corrected geometry failed final validity checks.", 0.0

            peak_density = float(high_edge_mask.mean())
            # Fewer dense peaks means a cleaner signal.
            peak_penalty = max(0.0, 1.0 - min(1.0, peak_density * 180.0))
            noise = max(edge_mean, 1e-6)
            snr = (edge_max / noise) - 1.0
            snr_score = min(1.0, math.log1p(snr) / math.log1p(25.0))
            shift_mag_px = math.hypot(dx_px, dy_px)
            center_score = 1.0 - min(1.0, shift_mag_px / max(1.0, float(self.config.max_shift_pixels)))
            confidence = (
                0.22
                + 0.54 * snr_score * peak_penalty
                + 0.24 * center_score
            )
            if selected_score > 0.0:
                confidence = (
                    0.58 * confidence
                    + 0.24 * phase_confidence
                    + 0.18 * selected_score
                )
            if boundary_signal_score > 0.0:
                confidence = 0.88 * confidence + 0.12 * boundary_signal_score
            confidence = clamp_confidence(confidence)

            if confidence < self.config.shift_threshold:
                return (
                    None,
                    f"Shift signal below confidence floor (dx={dx_m:.2f} m, dy={dy_m:.2f} m).",
                    confidence,
                )

            return (
                corrected,
                (
                    f"Estimated placement shift using local edge evidence ({hint_note}, {blend_note}): "
                    f"dx={dx_m:.3f}, dy={dy_m:.3f}, px={dx_px:.2f},{dy_px:.2f}, "
                    f"signal={edge_max:.3f}, conf={confidence:.3f}"
                ),
                confidence,
            )
        except Exception as exc:  # pragma: no cover
            return None, f"Shift estimation failed: {str(exc)[:140]}", 0.0

    def _estimate_alignment_prior(
        self,
        *,
        source,
        raster: Optional[RasterLoadResult],
        boundary_raster: Optional[RasterLoadResult],
        source_crs=None,
    ) -> Optional[AlignmentPrior]:
        if np is None or raster is None or boundary_raster is None:
            return None
        if raster.array is None or boundary_raster.array is None:
            return None
        try:
            from rasterio.transform import rowcol
        except Exception:
            return None

        total_rows = len(source) if hasattr(source, "__len__") else 0
        if total_rows <= 0:
            return None
        stride = max(1, int(math.ceil(total_rows / 420.0)))
        sampled = source.iloc[::stride] if hasattr(source, "iloc") else source

        observations: list[tuple[float, float, float, float]] = []
        samples = 0
        half_window = int(min(max(self.config.max_shift_pixels * 3, 28), 72))

        for _, row in sampled.iterrows():
            samples += 1
            geom = getattr(row, "geometry", None)
            if not self._is_valid_geometry(geom):
                continue
            target_geom = self._transform_geometry(geom, source_crs, raster.crs)
            if not self._is_valid_geometry(target_geom):
                continue

            try:
                cx, cy = target_geom.centroid.x, target_geom.centroid.y
                r, c = rowcol(raster.transform, cx, cy)
            except Exception:
                continue
            if r < 0 or c < 0 or r >= raster.height or c >= raster.width:
                continue

            r0 = max(0, r - half_window)
            r1 = min(raster.height, r + half_window)
            c0 = max(0, c - half_window)
            c1 = min(raster.width, c + half_window)
            shape = (r1 - r0, c1 - c0)
            if shape[0] < 24 or shape[1] < 24:
                continue

            boundary_patch = self._boundary_hint_patch(
                boundary_raster=boundary_raster,
                r0=r0,
                r1=r1,
                c0=c0,
                c1=c1,
                shape=shape,
            )
            if boundary_patch is None:
                continue
            template = self._rasterize_geometry_edges(
                geometry=target_geom,
                raster_transform=raster.transform,
                r0=r0,
                r1=r1,
                c0=c0,
                c1=c1,
                shape=shape,
            )
            grid = self._score_candidate_grid(
                edge_patch=boundary_patch,
                template=template,
                max_shift_pixels=self.config.max_shift_pixels,
                boundary_signal_score=0.0,
            )
            if grid is None:
                continue
            if grid["score_margin"] < 0.018 or grid["best_score"] < 0.035:
                continue
            if math.hypot(grid["dx_px"], grid["dy_px"]) < 1.0:
                continue
            weight = max(0.0, grid["score_margin"]) * (0.35 + grid["confidence"])
            if weight <= 0.0:
                continue
            observations.append((grid["dx_px"], grid["dy_px"], weight, grid["score_margin"]))

        if len(observations) < 12:
            return None

        dxs = np.array([item[0] for item in observations], dtype=np.float64)
        dys = np.array([item[1] for item in observations], dtype=np.float64)
        weights = np.array([item[2] for item in observations], dtype=np.float64)
        margins = np.array([item[3] for item in observations], dtype=np.float64)
        if float(weights.sum()) <= 0.0:
            return None

        med_dx = self._weighted_median(dxs, weights)
        med_dy = self._weighted_median(dys, weights)
        distances = np.hypot(dxs - med_dx, dys - med_dy)
        close = distances <= max(3.5, self.config.max_shift_pixels * 0.32)
        if not bool(close.any()):
            return None

        close_weight = float(weights[close].sum())
        total_weight = float(weights.sum())
        consistency = close_weight / max(total_weight, 1e-9)
        if consistency < 0.22:
            return None

        dx = float(np.average(dxs[close], weights=weights[close]))
        dy = float(np.average(dys[close], weights=weights[close]))
        support = int(close.sum())
        support_score = min(1.0, support / 80.0)
        mean_margin = float(np.average(margins[close], weights=weights[close]))
        margin_score = min(1.0, max(0.0, mean_margin) * 8.0)
        confidence = clamp_confidence(
            0.12
            + 0.48 * consistency
            + 0.22 * support_score
            + 0.18 * margin_score
        )
        if confidence < 0.34 or math.hypot(dx, dy) < 1.0:
            return None

        return AlignmentPrior(
            dx_px=dx,
            dy_px=dy,
            confidence=confidence,
            samples=samples,
            support=support,
            consistency=float(consistency),
            mean_margin=mean_margin,
        )

    @staticmethod
    def _boundary_hint_patch(
        *,
        boundary_raster: Optional[RasterLoadResult],
        r0: int,
        r1: int,
        c0: int,
        c1: int,
        shape,
    ):
        if np is None or boundary_raster is None or boundary_raster.array is None:
            return None
        if boundary_raster.height < r1 or boundary_raster.width < c1:
            return None
        patch = boundary_raster.array[r0:r1, c0:c1]
        if patch.shape != shape or patch.size == 0:
            return None
        if np.isnan(patch).all():
            return None
        return patch

    @staticmethod
    def _align_boundary_raster(
        boundary_raster: Optional[RasterLoadResult],
        raster: Optional[RasterLoadResult],
    ) -> Optional[RasterLoadResult]:
        if np is None or boundary_raster is None or raster is None:
            return boundary_raster
        if boundary_raster.array is None or raster.array is None:
            return boundary_raster

        same_grid = (
            boundary_raster.width == raster.width
            and boundary_raster.height == raster.height
            and str(boundary_raster.crs) == str(raster.crs)
            and tuple(boundary_raster.transform) == tuple(raster.transform)
        )
        if same_grid:
            return boundary_raster

        try:
            from rasterio.enums import Resampling
            from rasterio.warp import reproject

            destination = np.full((raster.height, raster.width), np.nan, dtype=np.float32)
            source = np.nan_to_num(boundary_raster.array.astype(np.float32), nan=0.0)
            reproject(
                source=source,
                destination=destination,
                src_transform=boundary_raster.transform,
                src_crs=boundary_raster.crs,
                dst_transform=raster.transform,
                dst_crs=raster.crs,
                resampling=Resampling.bilinear,
                src_nodata=0.0,
                dst_nodata=np.nan,
            )
            return RasterLoadResult(
                array=destination,
                transform=raster.transform,
                crs=raster.crs,
                width=raster.width,
                height=raster.height,
            )
        except Exception:
            return boundary_raster

    @staticmethod
    def _blend_boundary_hints(edge_strength, boundary_patch):
        edge = np.nan_to_num(edge_strength, nan=0.0).astype(np.float64)
        hints = np.nan_to_num(boundary_patch, nan=0.0).astype(np.float64)
        if edge.size == 0 or hints.size == 0:
            return edge_strength, 0.0

        edge_max = float(edge.max())
        hint_max = float(hints.max())
        if edge_max <= 0.0 or hint_max <= 0.0:
            return edge_strength, 0.0

        edge_norm = edge / edge_max
        hint_norm = hints / hint_max
        hint_active = hint_norm >= np.percentile(hint_norm, 98.0)
        hint_density = float(hint_active.mean())
        if hint_density <= 0.0:
            return edge_strength, 0.0

        # Keep satellite imagery primary; use hints only to strengthen nearby edge evidence.
        blended = (0.78 * edge_norm) + (0.22 * hint_norm)
        clarity = max(0.0, 1.0 - min(1.0, hint_density * 80.0))
        signal_score = clamp_confidence(0.25 + 0.55 * clarity)
        return blended.astype(np.float64), signal_score

    def _score_candidate_grid(
        self,
        *,
        edge_patch,
        template,
        max_shift_pixels: int,
        boundary_signal_score: float,
    ) -> Optional[dict]:
        if np is None or edge_patch is None or template is None:
            return None
        template = np.asarray(template)
        if template.size == 0 or float(template.sum()) <= 1.0:
            return None

        edge = np.nan_to_num(np.asarray(edge_patch), nan=0.0).astype(np.float64)
        edge_max = float(edge.max()) if edge.size else 0.0
        if edge_max <= 0.0:
            return None
        edge_norm = edge / edge_max

        yy, xx = np.where(template > 0)
        if len(xx) < 4:
            return None

        max_shift = int(max(1, max_shift_pixels))
        coarse_step = 2 if max_shift <= 24 else 3
        offsets: set[tuple[int, int]] = {(0, 0)}
        for dy in range(-max_shift, max_shift + 1, coarse_step):
            for dx in range(-max_shift, max_shift + 1, coarse_step):
                offsets.add((dx, dy))

        coarse = []
        for dx, dy in offsets:
            coarse.append((self._candidate_offset_score(edge_norm, xx, yy, dx, dy, max_shift), dx, dy))
        coarse.sort(reverse=True)

        refined_offsets: set[tuple[int, int]] = set(offsets)
        for _, dx, dy in coarse[:8]:
            for ddy in range(-2, 3):
                for ddx in range(-2, 3):
                    nx = int(dx + ddx)
                    ny = int(dy + ddy)
                    if abs(nx) <= max_shift and abs(ny) <= max_shift:
                        refined_offsets.add((nx, ny))

        scored = []
        for dx, dy in refined_offsets:
            score = self._candidate_offset_score(edge_norm, xx, yy, dx, dy, max_shift)
            scored.append((score, dx, dy))
        scored.sort(reverse=True)
        if not scored:
            return None

        baseline_score = self._candidate_offset_score(edge_norm, xx, yy, 0, 0, max_shift)
        best_score, best_dx, best_dy = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else baseline_score
        score_margin = float(best_score - baseline_score)
        second_margin = float(best_score - second_score)

        if best_dx == 0 and best_dy == 0:
            score_margin = 0.0

        confidence = (
            0.10
            + min(0.72, max(0.0, score_margin) * 5.5)
            + min(0.10, max(0.0, second_margin) * 2.8)
            + 0.08 * max(0.0, min(1.0, boundary_signal_score))
        )
        # A tiny runner-up gap is often just a one-pixel plateau around the same boundary.
        # Treat it as unsafe only when the absolute improvement over the official position is also modest.
        # If the official boundary itself has weak support, a near-threshold candidate can still be worth moving.
        low_official_near_threshold = baseline_score < 0.125 and score_margin >= 0.070
        if (
            (score_margin < 0.075 and not low_official_near_threshold)
            or (
                second_margin < 0.003
                and score_margin < 0.100
                and not low_official_near_threshold
            )
        ):
            confidence = min(confidence, 0.34)
        confidence = clamp_confidence(confidence)

        return {
            "dx_px": float(best_dx),
            "dy_px": float(best_dy),
            "baseline_score": float(baseline_score),
            "best_score": float(best_score),
            "score_margin": float(score_margin),
            "second_margin": float(second_margin),
            "confidence": confidence,
        }

    def _score_prior_candidate_grid(
        self,
        *,
        edge_patch,
        template,
        max_shift_pixels: int,
        boundary_signal_score: float,
        alignment_prior: Optional[AlignmentPrior],
    ) -> Optional[dict]:
        if alignment_prior is None or alignment_prior.confidence < 0.34:
            return None
        if np is None or edge_patch is None or template is None:
            return None
        template = np.asarray(template)
        if template.size == 0 or float(template.sum()) <= 1.0:
            return None

        edge = np.nan_to_num(np.asarray(edge_patch), nan=0.0).astype(np.float64)
        edge_max = float(edge.max()) if edge.size else 0.0
        if edge_max <= 0.0:
            return None
        edge_norm = edge / edge_max

        yy, xx = np.where(template > 0)
        if len(xx) < 4:
            return None

        max_shift = int(max(1, max_shift_pixels))
        center_dx = int(round(alignment_prior.dx_px))
        center_dy = int(round(alignment_prior.dy_px))
        if abs(center_dx) > max_shift or abs(center_dy) > max_shift:
            return None

        radius = 2 if alignment_prior.confidence >= 0.55 else 3
        offsets: set[tuple[int, int]] = {(center_dx, center_dy)}
        for dy in range(center_dy - radius, center_dy + radius + 1):
            for dx in range(center_dx - radius, center_dx + radius + 1):
                if abs(dx) <= max_shift and abs(dy) <= max_shift:
                    offsets.add((dx, dy))

        scored = [
            (self._candidate_offset_score(edge_norm, xx, yy, dx, dy, max_shift), dx, dy)
            for dx, dy in offsets
        ]
        scored.sort(reverse=True)
        if not scored:
            return None

        baseline_score = self._candidate_offset_score(edge_norm, xx, yy, 0, 0, max_shift)
        best_score, best_dx, best_dy = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else baseline_score
        score_margin = float(best_score - baseline_score)
        second_margin = float(best_score - second_score)
        if best_dx == 0 and best_dy == 0:
            score_margin = 0.0

        agreement_distance = math.hypot(best_dx - alignment_prior.dx_px, best_dy - alignment_prior.dy_px)
        agreement = max(0.0, 1.0 - agreement_distance / max(3.5, max_shift * 0.28))
        prior_strength = alignment_prior.confidence * agreement
        confidence = (
            0.11
            + min(0.46, max(0.0, score_margin) * 5.0)
            + min(0.07, max(0.0, second_margin) * 2.8)
            + 0.28 * prior_strength
            + 0.08 * max(0.0, min(1.0, boundary_signal_score))
        )
        if score_margin < 0.022 or agreement < 0.30:
            confidence = min(confidence, 0.34)
        if baseline_score >= 0.30 and score_margin < 0.055:
            confidence = min(confidence, 0.36)
        confidence = clamp_confidence(confidence)

        return {
            "dx_px": float(best_dx),
            "dy_px": float(best_dy),
            "baseline_score": float(baseline_score),
            "best_score": float(best_score),
            "score_margin": float(score_margin),
            "second_margin": float(second_margin),
            "confidence": confidence,
            "source": "global-prior constrained candidate-grid",
            "prior_agreement": float(agreement),
        }

    @staticmethod
    def _select_grid_candidate(local_grid: Optional[dict], prior_grid: Optional[dict]) -> Optional[dict]:
        if local_grid is None:
            return prior_grid
        if prior_grid is None:
            return local_grid
        if local_grid["confidence"] < 0.40 and prior_grid["confidence"] >= local_grid["confidence"]:
            return prior_grid
        if prior_grid["confidence"] > local_grid["confidence"] + 0.04:
            return prior_grid
        if local_grid["score_margin"] <= 0.0 and prior_grid["score_margin"] > 0.0:
            return prior_grid
        return local_grid

    @staticmethod
    def _weighted_median(values, weights) -> float:
        values = np.asarray(values, dtype=np.float64)
        weights = np.asarray(weights, dtype=np.float64)
        order = np.argsort(values)
        values = values[order]
        weights = weights[order]
        cumulative = np.cumsum(weights)
        cutoff = 0.5 * float(cumulative[-1])
        index = int(np.searchsorted(cumulative, cutoff, side="left"))
        index = max(0, min(index, len(values) - 1))
        return float(values[index])

    @staticmethod
    def _candidate_offset_score(edge_norm, xx, yy, dx: int, dy: int, max_shift: int) -> float:
        h, w = edge_norm.shape
        sx = xx + dx
        sy = yy + dy
        valid = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
        coverage = float(valid.mean()) if len(valid) else 0.0
        if coverage < 0.65:
            return 0.0

        values = edge_norm[sy[valid], sx[valid]]
        if values.size == 0:
            return 0.0
        mean_support = float(values.mean())
        upper_support = float(np.percentile(values, 75))
        alignment = 0.65 * mean_support + 0.35 * upper_support
        distance = math.hypot(float(dx), float(dy)) / max(1.0, float(max_shift))
        distance_penalty = 0.08 * (distance ** 1.2)
        return max(0.0, coverage * alignment - distance_penalty)

    def _is_valid_geometry(self, geometry) -> bool:
        try:
            return bool(geometry is not None and self._shapely.is_valid(geometry) and not geometry.is_empty)
        except Exception:
            return False

    @staticmethod
    def _pixels_to_meters(dx_pixels: float, dy_pixels: float, affine_transform) -> Tuple[float, float]:
        sx = float(getattr(affine_transform, "a", affine_transform[0]))
        sy = float(getattr(affine_transform, "e", affine_transform[4]))
        return dx_pixels * sx, dy_pixels * sy

    def _transform_geometry(self, geometry, source_crs, target_crs):
        if geometry is None or source_crs is None or target_crs is None:
            return geometry
        try:
            src = str(source_crs)
            dst = str(target_crs)
            if not src or src.lower() == "none" or not dst or dst.lower() == "none" or src == dst:
                return geometry
            from shapely.ops import transform
            transformer = pyproj().Transformer.from_crs(src, dst, always_xy=True)
            return transform(transformer.transform, geometry)
        except Exception:
            return None

    def _estimate_shift_by_phase_correlation(
        self,
        edge_patch,
        template,
    ) -> Optional[dict]:
        if np is None or edge_patch is None:
            return None

        h = edge_patch.shape[0]
        w = edge_patch.shape[1]
        if h < 12 or w < 12:
            return None

        if template is None:
            return None

        template = template.astype(np.float64)
        target = np.nan_to_num(edge_patch, nan=0.0).astype(np.float64)
        template_sum = float(template.sum())
        target_sum = float(target.sum())
        if template_sum <= 1.0 or target_sum <= 1.0:
            return None

        try:
            H, W = int(h * 2), int(w * 2)
            t_fft = np.fft.fft2(template - template.mean(), s=(H, W))
            r_fft = np.fft.fft2(target - target.mean(), s=(H, W))
            cross_power = t_fft * np.conj(r_fft)
            denom = np.abs(cross_power)
            denom[denom < 1e-12] = 1.0
            corr = np.abs(np.fft.ifft2(cross_power / denom)).real
        except Exception:
            return None

        peak_idx = int(np.argmax(corr))
        peak_y = peak_idx // W
        peak_x = peak_idx % W

        def _subpixel_offset(values, center_idx):
            if center_idx <= 0 or center_idx >= len(values) - 1:
                return 0.0
            left = values[center_idx - 1]
            center = values[center_idx]
            right = values[center_idx + 1]
            denom = (left - 2.0 * center + right)
            if math.fabs(denom) <= 1e-12:
                return 0.0
            return 0.5 * (left - right) / denom

        dy = float(peak_y + _subpixel_offset(corr[:, peak_x], peak_y))
        dx = float(peak_x + _subpixel_offset(corr[peak_y, :], peak_x))
        if dy > h:
            dy -= H
        if dx > w:
            dx -= W

        dy = -float(dy)
        dx = -float(dx)
        if (
            math.fabs(dx) > self.config.max_shift_pixels
            or math.fabs(dy) > self.config.max_shift_pixels
        ):
            return None

        peak = float(corr[peak_idx // W, peak_idx % W])
        noise = float(corr.mean())
        rel_snr = (peak - noise) / (noise + 1e-6)
        conf = clamp_confidence(min(0.99, max(0.0, rel_snr / 12.0)))
        if conf <= 0.05:
            return None

        return {
            "dx_px": dx,
            "dy_px": dy,
            "confidence": conf,
            "method_note": f"phase-corr candidate: dx={dx:.2f}, dy={dy:.2f}, conf={conf:.3f}",
        }

    @staticmethod
    def _edge_alignment_score(edge_patch, template, dx_px: float, dy_px: float) -> float:
        if np is None or edge_patch is None or template is None:
            return 0.0
        try:
            patch = np.nan_to_num(np.array(edge_patch), nan=0.0).astype(np.float64)
            shifted = BoundarySolver._shift_array(
                template.astype(np.float64),
                dx_px,
                dy_px,
                output_shape=patch.shape,
            )
        except Exception:
            return 0.0
        if shifted is None or patch.shape != shifted.shape:
            return 0.0

        template_shifted = shifted.astype(np.float64)
        patch = patch - patch.mean()
        template_shifted = template_shifted - template_shifted.mean()
        denom = math.sqrt(
            float((patch * patch).sum() * (template_shifted * template_shifted).sum()) + 1e-12
        )
        if denom <= 0.0:
            return 0.0
        score = float((patch * template_shifted).sum() / denom)
        if score <= 0.0:
            return 0.0
        if score > 1.0:
            score = 1.0
        return score

    @staticmethod
    def _shift_array(template, dx_px: float, dy_px: float, output_shape: Optional[tuple] = None):
        if np is None or template is None:
            return None
        template = np.asarray(template, dtype=np.float64)
        if template.size == 0:
            return None
        if output_shape is None:
            output_shape = template.shape
        out_h, out_w = output_shape

        try:
            from scipy.ndimage import shift as ndi_shift

            # Shift the template in template-space by candidate dx/dy and measure overlap.
            return ndi_shift(
                template.astype(np.float64),
                shift=(-dy_px, -dx_px),
                order=1,
                mode="constant",
                cval=0.0,
                prefilter=False,
            )
        except Exception:
            pass

        template_h = template.shape[0]
        template_w = template.shape[1]
        if template_h != out_h or template_w != out_w:
            return None

        shift_y = int(round(dy_px))
        shift_x = int(round(dx_px))
        if abs(shift_y) > template_h or abs(shift_x) > template_w:
            return np.zeros((out_h, out_w), dtype=np.float64)

        shifted = np.zeros((out_h, out_w), dtype=np.float64)
        dst_r0 = max(0, shift_y)
        dst_r1 = template_h + min(0, shift_y)
        src_r0 = max(0, -shift_y)
        src_r1 = template_h - max(0, -shift_y)

        dst_c0 = max(0, shift_x)
        dst_c1 = template_w + min(0, shift_x)
        src_c0 = max(0, -shift_x)
        src_c1 = template_w - max(0, -shift_x)

        if dst_r1 <= dst_r0 or dst_c1 <= dst_c0:
            return shifted
        shifted[dst_r0:dst_r1, dst_c0:dst_c1] = template[src_r0:src_r1, src_c0:src_c1]
        return shifted

    def _rasterize_geometry_edges(
        self,
        geometry,
        raster_transform,
        r0: int,
        r1: int,
        c0: int,
        c1: int,
        shape,
    ):
        try:
            from shapely.geometry import mapping
            from shapely.geometry.base import BaseGeometry
            from rasterio.features import rasterize
            from rasterio.transform import from_bounds
            from rasterio.transform import xy
        except Exception:
            return None

        if geometry is None or not hasattr(geometry, "geom_type"):
            return None
        if not isinstance(geometry, BaseGeometry):
            return None

        if geometry.geom_type.lower() not in {"polygon", "multipolygon", "linestring", "multilinestring"}:
            return None

        geom = geometry.boundary if hasattr(geometry, "boundary") else geometry
        try:
            x_left, y_top = xy(raster_transform, r0, c0, offset="ul")
            x_right, y_bottom = xy(raster_transform, r1, c1, offset="lr")
            window_transform = from_bounds(
                x_left,
                y_bottom,
                x_right,
                y_top,
                width=shape[1],
                height=shape[0],
            )
            return rasterize(
                [(mapping(geom), 1)],
                out_shape=shape,
                transform=window_transform,
                fill=0,
                all_touched=True,
                dtype=np.uint8,
            )
        except Exception:
            return None

    def _build_feature(self, row, decision: PlotDecision) -> dict:
        props = {}
        if hasattr(row, "to_dict"):
            props.update(row.to_dict())
        elif isinstance(row, dict):
            props.update(row.get("properties", {}))

        plot_id = props.get(self.config.plot_id_field, "")
        if plot_id is None:
            raise ValueError("plot_number missing; cannot identify feature.")

        props_out = {
            self.config.plot_id_field: plot_id,
            "status": decision.status,
            "confidence": clamp_confidence(
                max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, decision.confidence))
            ),
            "method_note": decision.method_note,
        }
        for key in list(props.keys()):
            if key not in props_out and key != "geometry":
                props_out[key] = props[key]

        return {
            "type": "Feature",
            "geometry": self._gpd.GeoSeries([decision.geometry]).iloc[0].__geo_interface__,
            "properties": props_out,
        }

    def _write_manifest(
        self,
        artifacts: SolveArtifacts,
        raster_used: bool,
        boundary_hints_used: bool,
        alignment_prior: Optional[AlignmentPrior],
    ) -> None:
        conf_stats = {
            "count": len(self._confidences),
            "min": float(min(self._confidences)) if self._confidences else 0.0,
            "max": float(max(self._confidences)) if self._confidences else 0.0,
            "mean": float(sum(self._confidences) / len(self._confidences)) if self._confidences else 0.0,
        }

        manifest = {
            "version": "0.1.0",
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runner": "bhume-boundary-assignment",
            "config": self.config.__dict__,
            "raster_used": raster_used,
            "boundary_hints_used": boundary_hints_used,
            "alignment_prior": None if alignment_prior is None else {
                "dx_px": alignment_prior.dx_px,
                "dy_px": alignment_prior.dy_px,
                "confidence": alignment_prior.confidence,
                "samples": alignment_prior.samples,
                "support": alignment_prior.support,
                "consistency": alignment_prior.consistency,
                "mean_margin": alignment_prior.mean_margin,
            },
            "counts": {
                "total": artifacts.total,
                "corrected": artifacts.corrected,
                "flagged": artifacts.flagged,
                "skipped": artifacts.skipped,
            },
            "confidence_stats": conf_stats,
            "geometry_repair": self._repair_stats.copy(),
            "elapsed_seconds": artifacts.elapsed_seconds,
        }
        artifacts.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(artifacts.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)

    def _normalize_geometry_for_output(self, geometry):
        if geometry is None:
            return None
        if self._is_valid_geometry(geometry):
            return geometry
        original_area = float(getattr(geometry, "area", 0.0))

        self._repair_stats["attempted"] += 1
        repaired = self._repair_geometry(geometry)
        if repaired is None:
            self._repair_stats["unrepairable"] += 1
            return None

        polygon_like = self._coerce_polygonal_geometry(repaired)
        if polygon_like is not None and self._is_valid_geometry(polygon_like):
            repaired_area = float(getattr(polygon_like, "area", 0.0))
            if original_area > 0 and repaired_area > 0:
                area_ratio = repaired_area / original_area
                if area_ratio < 0.50 or area_ratio > 2.25:
                    self._repair_stats["unrepairable"] += 1
                    return None
            self._repair_stats["repaired"] += 1
            return polygon_like

        return None

    def _repair_geometry(self, geometry):
        try:
            make_valid = getattr(self._shapely, "make_valid", None)
            if make_valid is not None:
                repaired = make_valid(geometry)
            else:
                repaired = geometry.buffer(0.0)
            if repaired is None or getattr(repaired, "is_empty", True):
                return None
            return repaired
        except Exception:
            try:
                repaired = geometry.buffer(0.0)
            except Exception:
                return None
            if repaired is None or getattr(repaired, "is_empty", True):
                return None
            return repaired

    def _coerce_polygonal_geometry(self, geometry):
        geom_type = getattr(geometry, "geom_type", "").lower()
        if geom_type in {"polygon", "multipolygon"}:
            return geometry
        if geom_type == "geometrycollection":
            geoms = [g for g in geometry.geoms if g.geom_type.lower() in {"polygon", "multipolygon"} and not g.is_empty]
            if not geoms:
                return None
            if len(geoms) == 1:
                return geoms[0]
            try:
                from shapely.ops import unary_union

                merged = unary_union(geoms)
            except Exception:
                return geoms[0]
            return merged if merged is not None else geoms[0]
        return None
