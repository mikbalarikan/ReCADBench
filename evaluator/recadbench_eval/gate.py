"""Gate G0 (fail => all scores 0). 03_SCORING §1.

Static checks (before alignment):
- STEP loads, exactly one solid, ``BRepCheck_Analyzer`` valid, closed shell;
- faceted / mesh-to-B-rep: ``faces_pred > 3 x faces_gt`` or > 50% of the planar faces are
  triangles (checked when the candidate has >= ``MIN_PLANAR`` planar faces);
- units / scale: the sorted principal extents of the candidate are within 20% of the GT's
  (rotation invariant, so it does not depend on the frame the agent modelled in; the GT is used
  rather than the raw scan, whose extents include stray patches and scanner-table remnants);
- non-parametric body: ``features.json`` missing (counts are never self-reported) or its
  ``unmapped`` non-empty.

Aligned check:
- freeform cover-up (tier 1-2 only): > 30% of the GT area that is plane / cylinder is matched
  (nearest candidate sample) to a B-spline / free-form candidate face.
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


def principal_extents(pts: np.ndarray) -> np.ndarray:
    c = pts.mean(axis=0)
    _, _, Vt = np.linalg.svd(pts - c, full_matrices=False)
    proj = (pts - c) @ Vt.T
    return np.sort(proj.max(axis=0) - proj.min(axis=0))[::-1]


def static_gate(g: GateResult, *, n_solids: int, valid: bool, closed: bool,
                recs: list[FaceRec] | None, n_triangles_planar: int, faces_gt: int,
                pred_vertices: np.ndarray | None, gt_vertices: np.ndarray,
                features: dict | None, cfg: EvalConfig) -> GateResult:
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
    if pred_vertices is not None and len(pred_vertices) >= 4:
        ep, eg = principal_extents(pred_vertices), principal_extents(gt_vertices)
        rel = np.abs(ep - eg) / np.maximum(eg, 1.0)
        g.checks["extent_rel_err"] = [round(float(x), 6) for x in rel]
        if float(rel.max()) > cfg.bbox_rel_tol:
            g.fail("bbox", f"extents {np.round(ep, 2).tolist()} vs GT {np.round(eg, 2).tolist()}"
                           f" (> {cfg.bbox_rel_tol:.0%}; units?)")
        else:
            g.passed("bbox")
    if features is None:
        g.fail("features", "features.json missing (feature counts come from the adapter only)")
    elif features.get("unmapped"):
        items = ", ".join(str(x) for x in features["unmapped"][:3])
        g.fail("unmapped", f"non-parametric body: unmapped = [{items}]")
    else:
        g.passed("unmapped")
    return g


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
