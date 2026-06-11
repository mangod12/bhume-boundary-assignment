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
        del boundaries
        include_flagged = self.config.include_flagged if include_flagged is None else include_flagged
        source = read_geojson(input_geojson)

        self._confidences = []
        self._repair_stats = {"attempted": 0, "repaired": 0, "unrepairable": 0}

        raster = read_raster(imagery) if imagery and imagery.exists() else None
        source_crs = getattr(source, "crs", None)

        out_features = []
        counters = {"total": 0, "corrected": 0, "flagged": 0, "skipped": 0}
        start = time.time()

        for _, row in source.iterrows():
            counters["total"] += 1
            decision = self._solve_plot(row, raster=raster, source_crs=source_crs)

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
        self._write_manifest(artifacts, bool(raster))
        return artifacts

    def _solve_plot(
        self,
        row,
        raster: Optional[RasterLoadResult],
        source_crs=None,
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
            source_crs=source_crs,
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
        source_crs=None,
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
                    f"Estimated placement shift using local edge evidence ({blend_note}): "
                    f"dx={dx_m:.3f}, dy={dy_m:.3f}, px={dx_px:.2f},{dy_px:.2f}, "
                    f"signal={edge_max:.3f}, conf={confidence:.3f}"
                ),
                confidence,
            )
        except Exception as exc:  # pragma: no cover
            return None, f"Shift estimation failed: {str(exc)[:140]}", 0.0

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

    def _write_manifest(self, artifacts: SolveArtifacts, raster_used: bool) -> None:
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
