"""Configuration and constants for the assignment pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

BASE_URL = "https://hiring.bhume.in"


VILLAGE_DATA = {
    "vadnerbhairav": {
        "input": "/data/34855_vadnerbhairav_chandavad_nashik/input.geojson",
        "imagery": "/data/34855_vadnerbhairav_chandavad_nashik/imagery.tif",
        "boundaries": "/data/34855_vadnerbhairav_chandavad_nashik/boundaries.tif",
        "truth": "/data/34855_vadnerbhairav_chandavad_nashik/example_truths.geojson",
        "slug": "34855_vadnerbhairav_chandavad_nashik",
    },
    "malatavadi": {
        "input": "/data/12429_malatavadi_chandgad_kolhapur/input.geojson",
        "imagery": "/data/12429_malatavadi_chandgad_kolhapur/imagery.tif",
        "boundaries": "/data/12429_malatavadi_chandgad_kolhapur/boundaries.tif",
        "truth": "/data/12429_malatavadi_chandgad_kolhapur/example_truths.geojson",
        "slug": "12429_malatavadi_chandgad_kolhapur",
    },
}

START_PAGE = "/start/"
TASK_PAGE = "/task/"
SUBMIT_PAGE = "/submit/"

AREA_FIELDS = (
    "map_area_sqm",
    "recorded_area_sqm",
    "recorded_area_ha",
    "area",
    "area_sqm",
    "area_m2",
    "area_sq_m",
    "area_sq_meters",
    "plot_area",
    "Shape_Area",
)

STATUS_CORRECTED = "corrected"
STATUS_FLAGGED = "flagged"

MAX_SHIFT_PIXELS = 18
SHIFT_CONF_THRESHOLD = 0.42
AREA_RATIO_LOW = 0.40
AREA_RATIO_HIGH = 1.75
CONFIDENCE_MIN = 0.05
CONFIDENCE_MAX = 0.95

PRESET_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "conservative": {
        "include_flagged": True,
        "min_confidence": 0.10,
        "max_shift_pixels": 14,
        "shift_threshold": 0.48,
        "area_ratio_low": 0.35,
        "area_ratio_high": 1.70,
        "strict": False,
    },
    "balanced": {
        "include_flagged": True,
        "min_confidence": 0.05,
        "max_shift_pixels": 18,
        "shift_threshold": 0.42,
        "area_ratio_low": 0.40,
        "area_ratio_high": 1.75,
        "strict": False,
    },
    "aggressive": {
        "include_flagged": True,
        "min_confidence": 0.02,
        "max_shift_pixels": 32,
        "shift_threshold": 0.30,
        "area_ratio_low": 0.35,
        "area_ratio_high": 1.90,
        "strict": False,
    },
    # Golden is the deployment default for unknown but similar scenarios.
    "golden": {
        "include_flagged": True,
        "min_confidence": 0.08,
        "max_shift_pixels": 20,
        "shift_threshold": 0.40,
        "area_ratio_low": 0.36,
        "area_ratio_high": 1.78,
        "strict": False,
    },
}

DEFAULT_PRESET = "golden"
PRESET_NAMES = tuple(PRESET_DEFINITIONS.keys())


@dataclass(frozen=True)
class SolverConfig:
    village_name: str
    plot_id_field: str = "plot_number"
    preset: str = DEFAULT_PRESET
    include_flagged: bool = True
    min_confidence: float = 0.0
    max_shift_pixels: int = MAX_SHIFT_PIXELS
    shift_threshold: float = SHIFT_CONF_THRESHOLD
    area_ratio_low: float = AREA_RATIO_LOW
    area_ratio_high: float = AREA_RATIO_HIGH
    strict: bool = False

    @property
    def id_prefix(self) -> str:
        return self.village_name.replace(" ", "_")

    @classmethod
    def preset_names(cls) -> tuple[str, ...]:
        return PRESET_NAMES

    @classmethod
    def from_dict(cls, value: dict, village_name: str) -> "SolverConfig":
        preset_name = str(value.get("preset", DEFAULT_PRESET))
        preset = PRESET_DEFINITIONS.get(preset_name)
        if preset is None:
            raise ValueError(f"Unknown preset '{preset_name}'. Available: {', '.join(PRESET_NAMES)}")
        payload = {
            **preset,
            **value,
            "village_name": village_name,
        }
        area_ratio_low = _validate_positive(payload.get("area_ratio_low", AREA_RATIO_LOW), "area_ratio_low")
        area_ratio_high = _validate_positive(payload.get("area_ratio_high", AREA_RATIO_HIGH), "area_ratio_high")
        validate_area_ratio_bounds(area_ratio_low, area_ratio_high)

        return cls(
            village_name=payload["village_name"],
            preset=payload.get("preset", DEFAULT_PRESET),
            include_flagged=payload.get("include_flagged", True),
            min_confidence=_validate_range(payload.get("min_confidence", 0.0), 0.0, 1.0, "min_confidence"),
            max_shift_pixels=_validate_non_negative_int(payload.get("max_shift_pixels", MAX_SHIFT_PIXELS), "max_shift_pixels"),
            shift_threshold=_validate_range(payload.get("shift_threshold", SHIFT_CONF_THRESHOLD), 0.0, 1.0, "shift_threshold"),
            area_ratio_low=area_ratio_low,
            area_ratio_high=area_ratio_high,
            strict=payload.get("strict", False),
            plot_id_field=payload.get("plot_id_field", "plot_number"),
        )


def _validate_range(value: float, minimum: float, maximum: float, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a numeric value.")
    value_f = float(value)
    if not (minimum <= value_f <= maximum):
        raise ValueError(f"{name} must be in the range [{minimum}, {maximum}].")
    return value_f


def _validate_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a numeric value.")
    value_f = float(value)
    if value_f <= 0:
        raise ValueError(f"{name} must be > 0.")
    return value_f


def _validate_non_negative_int(value: int, name: str) -> int:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be an integer.")
    value_i = int(value)
    if value_i <= 0:
        raise ValueError(f"{name} must be > 0.")
    return value_i


def validate_area_ratio_bounds(low: float, high: float) -> None:
    if low >= high:
        raise ValueError("area_ratio_low must be < area_ratio_high.")
