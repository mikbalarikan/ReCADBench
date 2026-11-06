"""Evaluator configuration: v1 thresholds from 03_SCORING.md (provisional, calibrated in P11).

Never tune these against the test split. Overrides: ``EvalConfig.from_file(path)`` (YAML/JSON,
any subset of the fields below) or keyword arguments.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

# 03_SCORING §3 S1: class -> (tight band, tight target, wide band, wide target)
CLASS_BANDS: dict[str, tuple[float, float, float, float]] = {
    "A": (0.05, 0.95, 0.10, 0.99),
    "B": (0.10, 0.95, 0.30, 0.99),
    "C": (0.30, 0.95, 0.50, 0.99),
}
# 03_SCORING §3 S3: key-dim tolerance by class (mm)
CLASS_KEYDIM_TOL: dict[str, float] = {"A": 0.01, "B": 0.02, "C": 0.05}


@dataclass(frozen=True)
class EvalConfig:
    seed: int = 20261106
    n_samples: int = 50000  # per direction (03_SCORING S1)
    icp_samples: int = 20000
    k_floor: float = 1.5  # band_eff = max(class band, k * scan_floor p95) (ADR 0002, provisional)
    chord_frac: float = 0.1  # tessellation chord = chord_frac * class tight band
    angular_rad: float = 0.25
    # gate (03_SCORING §1)
    faceted_face_ratio: float = 3.0
    faceted_triangle_share: float = 0.5
    freeform_share: float = 0.30
    bbox_rel_tol: float = 0.20
    # S1 curve
    s1_tight_floor: float = 0.60
    s1_wide_floor: float = 0.85
    # S2
    total_cap_lego: float = 40.0
    face_ratio_halve: float = 2.0
    param_coverage_min: float = 0.5
    # S3
    axis_tol_deg: float = 2.0
    coaxial_tol_mm: float = 0.01
    max_relations: int = 8
    symmetry_gt_tol_mm: float = 0.02
    # PASS (03_SCORING §4)
    pass_r_min: float = 0.5
    pass_r_max: float = 1.5
    pass_key_dims: float = 0.8
    # S5 (enabled from M5) — off unless asked for
    s5: bool = False

    @classmethod
    def from_file(cls, path: str | Path, **overrides) -> EvalConfig:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        data = json.loads(text) if p.suffix == ".json" else (yaml.safe_load(text) or {})
        data.update(overrides)
        return cls.with_overrides(**data)

    @classmethod
    def with_overrides(cls, **kw) -> EvalConfig:
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = set(kw) - names
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**kw)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def bands(tol_class: str) -> tuple[float, float, float, float]:
    if tol_class not in CLASS_BANDS:
        raise ValueError(f"unknown tolerance class {tol_class!r}")
    return CLASS_BANDS[tol_class]


def lin(x: float, lo: float, hi: float) -> float:
    """Linear ramp clipped to [0, 1]: lo -> 0, hi -> 1."""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))
