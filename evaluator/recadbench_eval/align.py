"""Candidate -> GT alignment (03_SCORING §2, ADR 0002).

1. The prediction is in the **scanner frame** (scan inputs): map it into the GT frame with
   ``meta.yaml: registration.T_scan_to_gt`` (identity for synthetic parts).
2. Fine rigid alignment: trimmed point-to-plane ICP of candidate samples onto the GT surface
   from that start (fixed seed).
3. Fallback when the start is poor (two-way F1 below ``fallback_f1``): the 24 proper PCA axis
   orientations (03_SCORING "inits = identity + 24 PCA"), coarse ICP then fine ICP each; the
   best by F1, then RMSE, wins. A candidate modelled in its own design frame still aligns, and
   ``datum_offset`` reports how far the final transform is from the registration.

Rigid only, no scale.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

from .brep import Tess, apply
from .geom import Surface, icp, rotation_delta, sample

FALLBACK_F1 = 0.8


@dataclass
class AlignResult:
    T_total: np.ndarray  # scanner (prediction) frame -> GT frame
    T_fine: np.ndarray  # correction applied after T_scan_to_gt
    rmse: float
    converged: bool
    f1: float
    init: str  # "registration" or "pca:<k>"
    datum_offset_mm: float
    datum_offset_deg: float


def _f1(pred_pts: np.ndarray, gt_surface: Surface, gt_pts_pred_frame: np.ndarray,
        pred_surface: Surface, band: float) -> float:
    """Two-way share within ``band``: candidate samples -> GT, GT samples -> candidate."""
    d1 = gt_surface.query(pred_pts, beyond_mm=band * 4)[0]
    d2 = pred_surface.query(gt_pts_pred_frame, beyond_mm=band * 4)[0]
    p, r = float(np.mean(d1 <= band)), float(np.mean(d2 <= band))
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _principal(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = pts.mean(axis=0)
    _, _, Vt = np.linalg.svd(pts - c, full_matrices=False)
    R = Vt.copy()
    if np.linalg.det(R) < 0:
        R[2] *= -1
    return c, R


def _pca_inits(src: np.ndarray, dst: np.ndarray) -> list[np.ndarray]:
    cs, Rs = _principal(src)
    cd, Rd = _principal(dst)
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            P = np.zeros((3, 3))
            for i, (j, sgn) in enumerate(zip(perm, signs, strict=True)):
                P[i, j] = sgn
            if np.linalg.det(P) < 0:
                continue
            R = Rd.T @ P @ Rs
            T = np.eye(4)
            T[:3, :3] = R
            T[:3, 3] = cd - R @ cs
            out.append(T)
    return out


def align(pred_tess_scan: Tess, T_scan_to_gt: np.ndarray, gt_surface: Surface,
          gt_pts: np.ndarray, band: float, *, n: int, seed: int) -> AlignResult:
    """Align a candidate tessellated in the scanner frame. ``band`` = effective tight band."""
    T0 = np.asarray(T_scan_to_gt, dtype=float)
    src = sample(pred_tess_scan, n, seed).points  # scanner frame
    pred_surface = Surface(pred_tess_scan)
    probe_gt = gt_pts[:: max(1, len(gt_pts) // 5000)]

    def run(T_init: np.ndarray, coarse: bool) -> tuple[np.ndarray, float, bool, float]:
        T = T_init
        if coarse:
            for cap in (20.0, 8.0, 3.0):
                T = icp(apply(T, src[:6000]), gt_surface, max_iter=15, trim_cap_mm=cap,
                        min_cut_mm=cap / 4).T @ T
        res = icp(apply(T, src), gt_surface)
        T = res.T @ T
        moved = apply(T, src)
        f1 = _f1(moved[::4], gt_surface, apply(np.linalg.inv(T), probe_gt), pred_surface, band)
        return T, res.rmse_mm, res.converged, f1

    T, rmse, conv, f1 = run(T0, coarse=False)
    init = "registration"
    if f1 < FALLBACK_F1:
        src_gt = apply(T0, src)
        for k, Tp in enumerate(_pca_inits(src_gt, gt_pts)):
            Tk, rk, ck, fk = run(Tp @ T0, coarse=True)
            if (fk, -rk) > (f1, -rmse):
                T, rmse, conv, f1, init = Tk, rk, ck, fk, f"pca:{k}"
    T_fine = T @ np.linalg.inv(T0)
    ddeg, dmm = rotation_delta(T_fine)
    return AlignResult(T, T_fine, rmse, conv, f1, init, dmm, ddeg)


def datum_offset(T_fine: np.ndarray) -> tuple[float, float]:
    deg, mm = rotation_delta(T_fine)
    return mm, deg


def is_rigid(T: np.ndarray, tol: float = 1e-6) -> bool:
    R = np.asarray(T)[:3, :3]
    return bool(np.allclose(R @ R.T, np.eye(3), atol=tol) and math.isclose(
        float(np.linalg.det(R)), 1.0, abs_tol=tol))
