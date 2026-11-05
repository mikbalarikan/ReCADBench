"""Scan ↔ GT registration, noise floor and observability (ADR 0002). Library for register_gt.py.

Runs on the evaluator host (GT-authoring). Deterministic: every random sample uses an explicit
seed. Approach ported (not imported) from mikbalarikan/agentic_STL-to-CAD `stl-re-verify`:
trimmed point-to-plane ICP, two-way unsigned distances, open-boundary and opposite-normal masks.

Conventions
- ``T`` is a 4×4 homogeneous rigid transform mapping **scan-frame** points into the **GT frame**:
  ``x_gt = T @ [x_scan, 1]`` (stored as ``meta.yaml: registration.T_scan_to_gt``).
- Distances are unsigned, in mm, measured to the closest point on the other mesh's triangles.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree

OPPOSITE_NORMAL_DEG = 70.0  # stl-re-verify mask M2


# ------------------------------------------------------------------------------- loading
def load_mesh(
    path: str | Path, *, chord_mm: float = 0.01, angular_deg: float = 2.0
) -> trimesh.Trimesh:
    """Load an STL/PLY/OBJ mesh, or tessellate a STEP file (needs build123d)."""
    p = Path(path)
    if p.suffix.lower() in (".step", ".stp"):
        return _tessellate_step(p, chord_mm, angular_deg)
    mesh = trimesh.load(p, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"{p}: not a triangle mesh")
    return mesh


def _tessellate_step(path: Path, chord_mm: float, angular_deg: float) -> trimesh.Trimesh:
    try:
        from build123d import import_step
    except ImportError as exc:  # pragma: no cover - depends on host
        raise RuntimeError(
            f"{path}: tessellating STEP needs build123d; install it or pass a CAD-exported mesh "
            "(--gt-mesh) instead"
        ) from exc
    shape = import_step(str(path))
    verts, tris = shape.tessellate(chord_mm, math.radians(angular_deg))
    v = np.array([[p.X, p.Y, p.Z] for p in verts], dtype=float)
    return trimesh.Trimesh(vertices=v, faces=np.asarray(tris, dtype=np.int64), process=False)


# ------------------------------------------------------------------------------ transforms
def apply(T: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ T[:3, :3].T + T[:3, 3]


def kabsch(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Best rigid T (no scale, no reflection) with T·src ≈ dst (least squares)."""
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    H = (src - cs).T @ (dst - cd)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = cd - R @ cs
    return T


def rotation_delta(T: np.ndarray) -> tuple[float, float]:
    """(angle in degrees, translation norm in mm) of a rigid T."""
    c = (np.trace(T[:3, :3]) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))), float(np.linalg.norm(T[:3, 3]))


# --------------------------------------------------------------------------------- distance
def closest(
    mesh: trimesh.Trimesh, pts: np.ndarray, chunk: int = 20000
) -> tuple[np.ndarray, np.ndarray]:
    """(distance, triangle id) of the closest point on ``mesh`` for each point (chunked)."""
    dist = np.empty(len(pts))
    tri = np.empty(len(pts), dtype=np.int64)
    for i in range(0, len(pts), chunk):
        _, d, t = trimesh.proximity.closest_point(mesh, pts[i : i + chunk])
        dist[i : i + chunk], tri[i : i + chunk] = d, t
    return dist, tri


def sample(mesh: trimesh.Trimesh, n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """``n`` area-uniform surface samples and their face normals."""
    pts, fid = trimesh.sample.sample_surface(mesh, n, seed=seed)
    return np.asarray(pts), np.asarray(mesh.face_normals[fid])


def stats(d: np.ndarray) -> dict[str, float]:
    if len(d) == 0:
        return {"n": 0}
    q = np.percentile(d, [50, 95, 99])
    return {
        "n": int(len(d)),
        "rms": float(np.sqrt(np.mean(d**2))),
        "p50": float(q[0]),
        "p95": float(q[1]),
        "p99": float(q[2]),
        "max": float(d.max()),
    }


# -------------------------------------------------------------------------------- coarse
def _principal_frame(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = pts.mean(axis=0)
    _, _, Vt = np.linalg.svd(pts - c, full_matrices=False)
    R = Vt  # rows = principal axes
    if np.linalg.det(R) < 0:
        R[2] *= -1
    return c, R


def coarse_pca(
    scan: trimesh.Trimesh, gt: trimesh.Trimesh, *, seed: int, n: int = 4000
) -> np.ndarray:
    """Coarse T by matching principal axes; tries the 24 proper axis flips/permutations and keeps
    the one with the lowest median scan→GT distance. Good enough to seed ICP for most parts;
    near-symmetric parts may need a manual initial transform instead."""
    s_pts, _ = sample(scan, n, seed)
    g_pts, _ = sample(gt, n, seed + 1)
    cs, Rs = _principal_frame(s_pts)
    cg, Rg = _principal_frame(g_pts)
    tree = cKDTree(g_pts)  # approximate distances are enough to rank coarse candidates
    probe = s_pts[:1500]
    best, best_med = np.eye(4), math.inf
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            P = np.zeros((3, 3))
            for i, (j, sgn) in enumerate(zip(perm, signs, strict=True)):
                P[i, j] = sgn
            if np.linalg.det(P) < 0:
                continue
            R = Rg.T @ P @ Rs
            T = np.eye(4)
            T[:3, :3] = R
            T[:3, 3] = cg - R @ cs
            med = float(np.median(tree.query(apply(T, probe))[0]))
            if med < best_med:
                best, best_med = T, med
    return best


# ----------------------------------------------------------------------------------- ICP
@dataclass
class IcpResult:
    T: np.ndarray
    converged: bool
    iterations: int
    rmse_mm: float
    kept_fraction: float
    history: list[dict] = field(default_factory=list)


def _point_to_plane_step(p: np.ndarray, q: np.ndarray, n: np.ndarray) -> np.ndarray:
    """One linearised point-to-plane step: rigid dT minimising Σ((dT·p − q)·n)²."""
    c = p.mean(axis=0)  # solve about the centroid for conditioning
    pc = p - c
    A = np.hstack([np.cross(pc, n), n])
    b = -np.einsum("ij,ij->i", p - q, n)
    x, *_ = np.linalg.lstsq(A, b, rcond=None)
    w, t = x[:3], x[3:]
    ang = float(np.linalg.norm(w))
    R = np.eye(3) if ang < 1e-15 else trimesh.transformations.rotation_matrix(ang, w / ang)[:3, :3]
    dT = np.eye(4)
    dT[:3, :3] = R
    dT[:3, 3] = c + t - R @ c
    return dT


def icp(
    scan: trimesh.Trimesh,
    gt: trimesh.Trimesh,
    T0: np.ndarray | None = None,
    *,
    seed: int = 11,
    n: int = 20000,
    max_iter: int = 60,
    trim_quantile: float = 0.9,
    trim_cap_mm: float | None = 2.0,
    tol_move_mm: float = 1e-3,  # 1 µm, far below scanner noise (~30–50 µm)
) -> IcpResult:
    """Trimmed point-to-plane ICP of scan samples onto the GT mesh (scan→GT direction).

    Correspondences beyond the ``trim_quantile`` distance (and beyond ``trim_cap_mm``) are
    dropped each iteration, so scan-only geometry (fixtures, skin flaps) does not pull the fit.
    Converged when the largest sample-point move of an iteration is below ``tol_move_mm``
    (measured on the part, so it does not depend on how far the part is from the origin).
    """
    pts, _ = sample(scan, n, seed)
    T = np.eye(4) if T0 is None else np.array(T0, dtype=float)
    hist: list[dict] = []
    converged, kept, rmse = False, 1.0, math.inf
    for it in range(1, max_iter + 1):
        moved = apply(T, pts)
        cp, d, tri = trimesh.proximity.closest_point(gt, moved)
        cut = np.quantile(d, trim_quantile)
        if trim_cap_mm is not None:
            cut = min(cut, max(trim_cap_mm, float(np.median(d)) * 3))
        keep = d <= cut
        kept = float(keep.mean())
        dT = _point_to_plane_step(moved[keep], cp[keep], gt.face_normals[tri[keep]])
        T = dT @ T
        rmse = float(np.sqrt(np.mean(d[keep] ** 2)))
        ddeg, _ = rotation_delta(dT)
        move = float(np.linalg.norm(apply(dT, moved) - moved, axis=1).max())
        hist.append({"it": it, "rmse": rmse, "kept": kept, "d_deg": ddeg, "max_move_mm": move})
        if move < tol_move_mm:
            converged = True
            break
    return IcpResult(
        T=T,
        converged=converged,
        iterations=len(hist),
        rmse_mm=rmse,
        kept_fraction=kept,
        history=hist,
    )


# ------------------------------------------------------------------- floor + observability
def boundary_faces(mesh: trimesh.Trimesh) -> np.ndarray:
    """Bool per face: has at least one edge used by only one face (scan hole / open boundary)."""
    idx = trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)
    flags = np.zeros(len(mesh.faces), dtype=bool)
    flags[mesh.edges_face[idx]] = True
    return flags


@dataclass
class FloorResult:
    scan_to_gt: dict
    gt_to_scan_all: dict
    gt_to_scan_observable: dict
    observable_fraction: float
    threshold_mm: float
    masks: dict
    gt_points: np.ndarray
    observable: np.ndarray


def floor_and_observability(
    scan: trimesh.Trimesh,
    gt: trimesh.Trimesh,
    T: np.ndarray,
    *,
    threshold_mm: float = 0.5,
    seed: int = 0,
    n_scan: int = 100000,
    n_gt: int = 100000,
) -> FloorResult:
    """Noise floor (GT's own deviation from the registered scan) and GT observability.

    A GT sample is **observable** if the registered scan has a surface within ``threshold_mm``
    that is not an open-boundary triangle reached from beyond half the threshold (mask M1) and
    does not face away by more than 70° (mask M2; scan normals may be unoriented, so |cos| is used).
    """
    scan_gt = scan.copy()
    scan_gt.apply_transform(T)
    s_pts, _ = sample(scan_gt, n_scan, seed)
    d_s2g, _ = closest(gt, s_pts)

    g_pts, g_nrm = sample(gt, n_gt, seed + 1)
    d_g2s, tri = closest(scan_gt, g_pts)
    cosang = np.abs(np.einsum("ij,ij->i", g_nrm, scan_gt.face_normals[tri]))
    near = d_g2s <= threshold_mm
    m1_open = boundary_faces(scan_gt)[tri] & (d_g2s > threshold_mm / 2)
    m2_normal = cosang < math.cos(math.radians(OPPOSITE_NORMAL_DEG))
    observable = near & ~m1_open & ~m2_normal
    return FloorResult(
        scan_to_gt=stats(d_s2g),
        gt_to_scan_all=stats(d_g2s),
        gt_to_scan_observable=stats(d_g2s[observable]),
        observable_fraction=float(observable.mean()),
        threshold_mm=threshold_mm,
        masks={
            "M0_far": float((~near).mean()),
            "M1_open_boundary": float((near & m1_open).mean()),
            "M2_opposite_normal": float((near & ~m1_open & m2_normal).mean()),
        },
        gt_points=g_pts,
        observable=observable,
    )
