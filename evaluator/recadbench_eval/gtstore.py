"""Ground-truth access. The ONLY module that reads GT, and only below ``RECADBENCH_GT_DIR``.

Layout (02_REPO_LAYOUTS, recadbench-gt): ``<GT_DIR>/<part_id>/{gt.step, gt_plan.json,
key_dims.yaml, meta.yaml, observability_mask.npz}``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

ENV = "RECADBENCH_GT_DIR"


class GtUnavailable(RuntimeError):
    """``RECADBENCH_GT_DIR`` is unset or does not hold the requested part."""


def gt_root() -> Path:
    root = os.environ.get(ENV)
    if not root:
        raise GtUnavailable(f"{ENV} is not set: the evaluator reads ground truth only from there")
    p = Path(root)
    if not p.is_dir():
        raise GtUnavailable(f"{ENV}={root!r} is not a directory")
    return p


def available() -> bool:
    try:
        gt_root()
    except GtUnavailable:
        return False
    return True


@dataclass
class GtPart:
    part_id: str
    folder: Path
    meta: dict
    key_dims: dict
    plan: dict | None
    step_path: Path
    mask_points: np.ndarray | None  # (n, 3) GT-frame sample points of the observability mask
    mask_observable: np.ndarray | None  # (n,) bool

    @property
    def is_scan(self) -> bool:
        return self.meta.get("source_kind") != "synthetic"

    @property
    def T_scan_to_gt(self) -> np.ndarray:
        reg = self.meta.get("registration") or {}
        T = reg.get("T_scan_to_gt")
        return np.eye(4) if T is None or not self.is_scan else np.asarray(T, dtype=float)

    @property
    def floor_p95(self) -> float:
        fl = self.meta.get("scan_floor") or {}
        return float(fl.get("scan_to_gt_p95_mm", 0.0)) if self.is_scan else 0.0


def load_part(part_id: str) -> GtPart:
    folder = gt_root() / part_id
    if not folder.is_dir():
        raise GtUnavailable(f"part {part_id!r} not found under ${ENV}")
    meta = _yaml(folder / "meta.yaml")
    key_dims = _yaml(folder / "key_dims.yaml")
    plan_p = folder / "gt_plan.json"
    plan = json.loads(plan_p.read_text(encoding="utf-8")) if plan_p.is_file() else None
    step = folder / "gt.step"
    if not step.is_file():
        raise GtUnavailable(f"{part_id}: gt.step missing")
    pts = obs = None
    mask_name = (meta.get("observability") or {}).get("mask_file")
    if mask_name and (folder / mask_name).is_file():
        with np.load(folder / mask_name) as z:
            pts = np.asarray(z["gt_points"], dtype=float)
            obs = np.asarray(z["observable"], dtype=bool)
    return GtPart(part_id, folder, meta, key_dims, plan, step, pts, obs)


def _yaml(p: Path) -> dict:
    if not p.is_file():
        raise GtUnavailable(f"{p.name} missing in {p.parent.name}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
