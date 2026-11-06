"""Gate G0 (fail => all scores 0). 03_SCORING §1.

Static checks (before alignment):
- STEP loads, exactly one solid, ``BRepCheck_Analyzer`` valid, closed shell, and the solid has
  exactly one shell (b2 = 0: no internal voids / hollow skins; 03_SCORING S4 relies on it);
- faceted / mesh-to-B-rep: ``faces_pred > 3 x faces_gt`` or > 50% of the planar faces are
  triangles (checked when the candidate has >= ``MIN_PLANAR`` planar faces);
- units / scale (rotation invariant, so independent of the frame the agent modelled in): the
  linear volume scale ``cbrt(V_pred / V_gt)`` within ``bbox_rel_tol`` of 1 (an inch/mm slip is
  25.4x; also a volume sanity check, so a thin hollow skin cannot hide behind surface distance);
- ``features.json`` readable (UTF-8, a BOM is accepted), valid against the spec ``features``
  schema (incl. the ``count`` consistency rule), present (counts are never self-reported) and its
  ``unmapped`` empty (non-parametric body).

Aligned checks:
- bbox: per-axis extents of the aligned candidate (GT frame) within ``bbox_rel_tol`` of the GT's
  (03_SCORING: "bbox within 20%"; done after alignment so it compares like with like; the GT is
  used rather than the raw scan, whose extents include stray patches and scanner-table remnants);
- freeform cover-up (tier 1-2 only): > 30% of the GT area that is plane / cylinder is matched
  (nearest candidate sample) to a B-spline / free-form candidate face.

GT-copy backstop (after the two-way deviation, ``gt_copy_gate``): volume and face count equal
to the GT's within tight tolerances AND the two-way P95 below a fraction of the scan noise floor
= ``suspected GT copy`` (the run reached gt.step; the runner's code checks are best effort).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .brep import FaceRec
from .config import EvalConfig

MIN_PLANAR = 8


@dataclass
class GateResult:
    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    faces_pred: int | None = None
    faces_gt: int | None = None

    def fail(self, check: str, reason: str) -> None:
        self.ok = False
        self.checks[check] = False
        self.reasons.append(reason)

    def passed(self, check: str) -> None:
        self.checks.setdefault(check, True)

    @property
    def reason(self) -> str | None:
        return "; ".join(self.reasons) if self.reasons else None


def static_gate(g: GateResult, *, n_solids: int, valid: bool, closed: bool,
                recs: list[FaceRec] | None, n_triangles_planar: int, faces_gt: int,
                pred_volume: float | None, gt_volume: float, features: dict | None,
                cfg: EvalConfig, n_shells: int = 1, features_error: str | None = None
                ) -> GateResult:
    g.faces_gt = int(faces_gt)
    if n_solids != 1:
        g.fail("one_solid", f"{n_solids} solids (exactly 1 required)")
    else:
        g.passed("one_solid")
    if not valid:
        g.fail("valid", "BRepCheck_Analyzer: invalid shape")
    else:
        g.passed("valid")
    if not closed:
        g.fail("closed", "open shell")
    else:
        g.passed("closed")
    if n_solids == 1:
        if n_shells != 1:
            g.fail("b2", f"internal void (b2 != 0): the solid has {n_shells} shells")
        else:
            g.passed("b2")
    if recs is not None:
        g.faces_pred = len(recs)
        planar = sum(1 for r in recs if r.kind == "plane")
        tri_share = n_triangles_planar / planar if planar else 0.0
        g.checks["triangle_share"] = round(tri_share, 6)
        if len(recs) > cfg.faceted_face_ratio * faces_gt:
            g.fail("faceted", f"faceted: {len(recs)} faces > {cfg.faceted_face_ratio:g} x "
                              f"{faces_gt} GT faces")
        elif planar >= MIN_PLANAR and tri_share > cfg.faceted_triangle_share:
            g.fail("faceted", f"faceted: {tri_share:.0%} of planar faces are triangles")
        else:
            g.passed("faceted")
    if pred_volume is not None and gt_volume > 0:
        scale = (abs(pred_volume) / gt_volume) ** (1.0 / 3.0)
        g.checks["volume_scale"] = round(float(scale), 6)
        if abs(scale - 1.0) > cfg.bbox_rel_tol:
            g.fail("volume", f"volume {abs(pred_volume):.6g} vs GT {gt_volume:.6g} mm3 (linear "
                             f"scale {scale:.3f}, > {cfg.bbox_rel_tol:.0%}; units?)")
        else:
            g.passed("volume")
    if features_error is not None:
        g.fail("features", features_error)
    elif features is None:
        g.fail("features", "features.json missing (feature counts come from the adapter only)")
    elif features.get("unmapped"):
        items = ", ".join(str(x) for x in features["unmapped"][:3])
        g.fail("unmapped", f"non-parametric body: unmapped = [{items}]")
    else:
        g.passed("unmapped")
    return g


def bbox_gate(g: GateResult, pred_pts: np.ndarray, gt_pts: np.ndarray, cfg: EvalConfig
              ) -> GateResult:
    """Per-axis extents of the aligned candidate vs the GT (both in the GT frame)."""
    ep = pred_pts.max(axis=0) - pred_pts.min(axis=0)
    eg = gt_pts.max(axis=0) - gt_pts.min(axis=0)
    rel = np.abs(ep - eg) / np.maximum(eg, 1.0)
    g.checks["extent_rel_err"] = [round(float(x), 6) for x in rel]
    if float(rel.max()) > cfg.bbox_rel_tol:
        g.fail("bbox", f"aligned extents {np.round(ep, 2).tolist()} vs GT "
                       f"{np.round(eg, 2).tolist()} (> {cfg.bbox_rel_tol:.0%}; units?)")
    else:
        g.passed("bbox")
    return g


def gt_copy_gate(g: GateResult, *, pred_volume: float, gt_volume: float, faces_pred: int,
                 faces_gt: int, p95_all: float, floor_p95: float, tight_class: float,
                 cfg: EvalConfig, enforce: bool = True) -> dict:
    """The GT-copy backstop; returns the measurements (always reported in details). It fails
    the gate only when ``cfg.gt_copy_check`` and ``enforce`` (the caller: not Track T, where
    the agent executes the reference plan and a GT-identical result is the expected one)."""
    vol_rel = abs(abs(pred_volume) - gt_volume) / gt_volume if gt_volume > 0 else None
    faces_rel = abs(faces_pred - faces_gt) / max(faces_gt, 1)
    limit = cfg.gt_copy_floor_frac * max(float(floor_p95 or 0.0), tight_class)
    suspected = bool(vol_rel is not None and vol_rel <= cfg.gt_copy_volume_rel
                     and faces_rel <= cfg.gt_copy_faces_rel and p95_all < limit)
    out = {"volume_rel": vol_rel, "faces_pred": int(faces_pred), "faces_gt": int(faces_gt),
           "faces_rel": faces_rel, "p95_all_mm": p95_all, "p95_limit_mm": limit,
           "floor_p95_mm": float(floor_p95 or 0.0), "suspected": suspected,
           "enforced": bool(cfg.gt_copy_check and enforce)}
    if cfg.gt_copy_check and enforce and suspected:
        g.fail("gt_copy", f"suspected GT copy: volume within {vol_rel:.2e}, {faces_pred} vs "
                          f"{faces_gt} faces, two-way P95 {p95_all:.4f} mm < {limit:.4f} mm "
                          "(far below the scan noise floor)")
    elif cfg.gt_copy_check and enforce:
        g.passed("gt_copy")
    return out


def freeform_gate(g: GateResult, *, tier: int, gt_pts: np.ndarray, gt_kind: np.ndarray,
                  pred_pts: np.ndarray, pred_kind: np.ndarray, cfg: EvalConfig) -> GateResult:
    """``gt_kind`` / ``pred_kind``: face type per sample (aligned, GT frame)."""
    if tier not in (1, 2):
        g.checks["freeform_share"] = None
        return g
    sel = np.isin(gt_kind, ["plane", "cylinder"])
    if not sel.any():
        g.checks["freeform_share"] = 0.0
        return g
    _, idx = cKDTree(pred_pts).query(gt_pts[sel])
    share = float(np.mean(pred_kind[idx] == "bspline"))
    g.checks["freeform_share"] = round(share, 6)
    if share > cfg.freeform_share:
        g.fail("freeform", f"freeform cover-up: {share:.0%} of GT plane/cylinder area is "
                           "B-spline in the candidate")
    else:
        g.passed("freeform")
    return g
