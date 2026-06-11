#!/usr/bin/env python3
"""Render deterministic visual review panels for BhuMe predictions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.windows import Window, bounds, from_bounds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render BhuMe prediction review panels.")
    parser.add_argument("--village", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--truth", type=Path, default=None)
    parser.add_argument("--max-panels", type=int, default=18)
    return parser.parse_args()


def load_gdf(path: Path, fallback_crs="EPSG:4326"):
    gdf = gpd.read_file(str(path))
    if "plot_number" not in gdf.columns:
        raise ValueError(f"{path} does not include plot_number")
    if gdf.crs is None:
        gdf = gdf.set_crs(fallback_crs)
    return gdf.set_index("plot_number", drop=False)


def select_plots(pred, truth, max_panels: int):
    selected = []
    seen = set()

    def add(plot_number, reason):
        if plot_number in seen or plot_number not in pred.index:
            return
        seen.add(plot_number)
        selected.append((plot_number, reason))

    if truth is not None:
        for plot_number in list(truth.index)[:max_panels]:
            add(plot_number, "public_truth")

    corrected = pred[pred["status"] == "corrected"].sort_values("confidence", ascending=False)
    for plot_number in corrected.index[: max(3, max_panels // 3)]:
        add(plot_number, "top_confidence_corrected")

    if len(corrected) > 0:
        mid = corrected.iloc[max(0, len(corrected) // 2 - 2) : len(corrected) // 2 + 3]
        for plot_number in mid.index:
            add(plot_number, "mid_confidence_corrected")

    flagged = pred[pred["status"] == "flagged"].sort_values("confidence", ascending=False)
    for plot_number in flagged.index[: max(3, max_panels // 4)]:
        add(plot_number, "high_confidence_flagged")

    return selected[:max_panels]


def safe_window(src, geom_bounds):
    minx, miny, maxx, maxy = geom_bounds
    width = max(maxx - minx, 1e-9)
    height = max(maxy - miny, 1e-9)
    pad_x = width * 0.45
    pad_y = height * 0.45
    window = from_bounds(
        minx - pad_x,
        miny - pad_y,
        maxx + pad_x,
        maxy + pad_y,
        transform=src.transform,
    ).round_offsets().round_lengths()
    col_off = max(0, int(window.col_off))
    row_off = max(0, int(window.row_off))
    col_end = min(src.width, int(window.col_off + window.width))
    row_end = min(src.height, int(window.row_off + window.height))
    if col_end <= col_off or row_end <= row_off:
        return Window(0, 0, src.width, src.height)
    return Window(col_off, row_off, col_end - col_off, row_end - row_off)


def plot_boundary(ax, geom, label, color, linestyle="-", linewidth=2.0):
    if geom is None or getattr(geom, "is_empty", True):
        return
    gpd.GeoSeries([geom]).boundary.plot(
        ax=ax,
        color=color,
        linestyle=linestyle,
        linewidth=linewidth,
        label=label,
    )


def render_panel(src, original_row, pred_row, truth_row, out_path: Path, title: str):
    geoms = [original_row.geometry, pred_row.geometry]
    if truth_row is not None:
        geoms.append(truth_row.geometry)
    union_bounds = gpd.GeoSeries(geoms).total_bounds
    window = safe_window(src, tuple(union_bounds))
    image = src.read(1, window=window).astype(float)
    if src.nodata is not None:
        image = np.where(image == src.nodata, np.nan, image)

    left, bottom, right, top = bounds(window, src.transform)
    fig, ax = plt.subplots(figsize=(8, 8), dpi=140)
    ax.imshow(image, cmap="gray", extent=(left, right, bottom, top), origin="upper")
    plot_boundary(ax, original_row.geometry, "official input", "#ff4d4d", "--", 2.0)
    plot_boundary(ax, pred_row.geometry, "prediction", "#00d4ff", "-", 2.4)
    if truth_row is not None:
        plot_boundary(ax, truth_row.geometry, "truth", "#ffe66d", "-", 2.0)

    ax.set_title(title, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.legend(loc="lower left", fontsize=7, framealpha=0.8)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    input_gdf = load_gdf(args.village / "input.geojson")
    pred = load_gdf(args.predictions, fallback_crs=input_gdf.crs)
    truth = load_gdf(args.truth, fallback_crs=input_gdf.crs) if args.truth and args.truth.exists() else None

    with rasterio.open(args.village / "imagery.tif") as src:
        raster_crs = src.crs
        if raster_crs is not None:
            input_plot = input_gdf.to_crs(raster_crs)
            pred_plot = pred.to_crs(raster_crs)
            truth_plot = truth.to_crs(raster_crs) if truth is not None else None
        else:
            input_plot = input_gdf
            pred_plot = pred
            truth_plot = truth

        selected = select_plots(pred, truth, args.max_panels)
        index = []
        for i, (plot_number, reason) in enumerate(selected, start=1):
            if plot_number not in input_plot.index or plot_number not in pred_plot.index:
                continue
            truth_row = truth_plot.loc[plot_number] if truth_plot is not None and plot_number in truth_plot.index else None
            pred_row = pred_plot.loc[plot_number]
            filename = f"{i:02d}_{reason}_{str(plot_number).replace('/', '_')}.png"
            out_path = args.out_dir / filename
            title = (
                f"{plot_number} | {reason} | status={pred.loc[plot_number]['status']} "
                f"| confidence={float(pred.loc[plot_number]['confidence']):.3f}"
            )
            render_panel(src, input_plot.loc[plot_number], pred_row, truth_row, out_path, title)
            index.append(
                {
                    "plot_number": str(plot_number),
                    "reason": reason,
                    "status": str(pred.loc[plot_number]["status"]),
                    "confidence": float(pred.loc[plot_number]["confidence"]),
                    "file": filename,
                }
            )

    (args.out_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"rendered {len(index)} panels to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
