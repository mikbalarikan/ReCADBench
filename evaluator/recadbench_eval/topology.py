"""S4 topology (10). 03_SCORING §3 S4.

- Through-hole / handle count b1 match (4): b1 of the candidate solid (genus of its closed
  boundary, from B-rep topology) equals ``meta.yaml: b1``. b0 = 1 and b2 = 0 (one shell) are
  gate checks.
- Face-type histogram similarity ``1 - TVD(pred, gt)`` over {plane, cylinder, cone, sphere,
  torus, bspline} by area (6). Any other free-form surface counts as bspline.
"""

from __future__ import annotations

from .brep import FACE_TYPES


def tvd(p: dict[str, float], q: dict[str, float]) -> float:
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in FACE_TYPES)


def s4(b1_pred: int, b1_gt: int, hist_pred: dict[str, float], hist_gt: dict[str, float]) -> dict:
    t = tvd(hist_pred, hist_gt)
    match = b1_pred == b1_gt
    return {
        "S4": (4.0 if match else 0.0) + 6.0 * (1.0 - t),
        "b1_match": match,
        "b1_pred": b1_pred,
        "b1_gt": b1_gt,
        "face_type_tvd": t,
        "face_types_pred": hist_pred,
    }
