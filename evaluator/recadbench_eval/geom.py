"""Point sampling, exact point-to-surface distances and rigid ICP on tessellated B-reps.

Ported (not imported) from ``dataset/tools/scanreg.py`` (trimmed point-to-plane ICP) and the
``stl-re-verify`` / retk ``self_deviation`` approach (refined triangle pieces + KD-tree, exact
nearest-triangle distances with a certificate). The evaluator measures CAD against CAD, so the
surfaces are clean and the simpler certificate below is enough.

Determinism: every random draw takes an explicit seed (``numpy.random.default_rng``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .brep import Tess, apply


# ------------------------------------------------------------------------------------ sampling
@dataclass
class Samples:
    points: np.ndarray  # (n, 3)
    normals: np.ndarray  # (n, 3) outward triangle normals
    face_id: np.ndarray  # (n,) B-rep face index


def _tri_normals(tri: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1)
    out = np.zeros_like(n)
    ok = ln > 0
    out[ok] = n[ok] / ln[ok, None]
    return out, 0.5 * ln


def sample(tess: Tess, n: int, seed: int) -> Samples:
    """``n`` area-uniform samples on the tessellated surface (fixed seed)."""
    tri = tess.tri_xyz
    nrm, area = _tri_normals(tri)
    rng = np.random.default_rng(seed)
    cdf = np.cumsum(area)
    pick = np.searchsorted(cdf, rng.random(n) * cdf[-1], side="right")
    pick = np.minimum(pick, len(area) - 1)
    r1 = np.sqrt(rng.random(n))
    r2 = rng.random(n)
    a, b, c = tri[pick, 0], tri[pick, 1], tri[pick, 2]
    pts = (1 - r1)[:, None] * a + (r1 * (1 - r2))[:, None] * b + (r1 * r2)[:, None] * c
    return Samples(pts, nrm[pick], tess.face_id[pick])


# ---------------------------------------------------------------------------- exact distances
def _closest_on_triangles(tri: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Closest point on each triangle ``tri[i]`` to ``p[i]`` (Ericson, vectorised)."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = p - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = p - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    out = np.empty_like(p)
    done = np.zeros(len(p), dtype=bool)

    def put(mask, val):
        m = mask & ~done
        out[m] = val[m] if val.ndim == 2 else val
        done[m] = True

    with np.errstate(divide="ignore", invalid="ignore"):
        put((d1 <= 0) & (d2 <= 0), a)
        put((d3 >= 0) & (d4 <= d3), b)
        v = d1 / (d1 - d3)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + v[:, None] * ab)
        put((d6 >= 0) & (d5 <= d6), c)
        w = d2 / (d2 - d6)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + w[:, None] * ac)
        w2 = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + w2[:, None] * (c - b))
        denom = va + vb + vc
        denom = np.where(np.abs(denom) < 1e-300, 1e-300, denom)
        vv = vb / denom
        ww = vc / denom
        inner = a + vv[:, None] * ab + ww[:, None] * ac
    out[~done] = inner[~done]
    return out


def _refine(tri: np.ndarray, fid: np.ndarray, h: float) -> tuple[np.ndarray, np.ndarray]:
    """Longest-edge bisection until no edge is longer than ``h`` (same surface, a soup)."""
    keep_t, keep_f = [], []
    for _ in range(40):
        e = np.linalg.norm(tri[:, [1, 2, 0]] - tri, axis=2)  # edges 01, 12, 20
        big = e.max(axis=1) > h
        keep_t.append(tri[~big])
        keep_f.append(fid[~big])
        if not big.any():
            break
        t, f, e = tri[big], fid[big], e[big]
        j = np.argmax(e, axis=1)  # longest edge j joins v_j and v_j+1
        r = (np.arange(3)[None, :] + j[:, None]) % 3
        t = np.take_along_axis(t, r[:, :, None], axis=1)  # (A, B, C) with AB longest
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        m = 0.5 * (a + b)
        tri = np.concatenate([np.stack([a, m, c], 1), np.stack([m, b, c], 1)])
        fid = np.concatenate([f, f])
    else:
        keep_t.append(tri)
        keep_f.append(fid)
    return np.concatenate(keep_t), np.concatenate(keep_f)


class Surface:
    """Exact nearest-point queries on a tessellated surface.

    Triangles are refined so no edge exceeds ``h`` (a soup with the same surface) and indexed
    by centroid. For a query the ``k`` nearest pieces give an upper bound ``u``; every piece
    whose centroid lies within ``u + reach_max`` might be closer, so those are checked exactly
    when the kNN set does not already certify the answer. Points farther than ``beyond_mm``
    get the kNN upper bound (they are far outside every scoring band).
    """

    def __init__(self, tess: Tess, max_pieces: int = 120000, k: int = 12) -> None:
        tri = tess.tri_xyz
        _, area = _tri_normals(tri)
        h = max(2.0 * math.sqrt(float(area.sum()) / max_pieces), 1e-3)
        self.tris, self.fid = _refine(tri, tess.face_id, h)
        self.nrm, _ = _tri_normals(self.tris)
        cent = self.tris.mean(axis=1)
        self.reach_max = float(np.linalg.norm(self.tris - cent[:, None, :], axis=2).max())
        self.tree = cKDTree(cent)
        self.k = min(k, len(cent))

    def query(
        self, pts: np.ndarray, beyond_mm: float = 2.0, chunk: int = 25000
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(distance, closest point, piece normal, B-rep face id) for each point."""
        n = len(pts)
        dist = np.empty(n)
        cpt = np.empty((n, 3))
        piece = np.empty(n, dtype=np.int64)
        for a in range(0, n, chunk):
            P = pts[a : a + chunk]
            m = len(P)
            dk, ik = self.tree.query(P, k=self.k)
            dk, ik = dk.reshape(m, -1), ik.reshape(m, -1)
            rep = np.repeat(np.arange(m), ik.shape[1])
            q = _closest_on_triangles(self.tris[ik.ravel()], P[rep])
            dd = np.linalg.norm(q - P[rep], axis=1).reshape(m, -1)
            j = np.argmin(dd, axis=1)
            best = dd[np.arange(m), j]
            bq = q.reshape(m, -1, 3)[np.arange(m), j]
            bi = ik[np.arange(m), j]
            # certified unless an unseen piece could be closer (centroid within best + reach)
            todo = np.nonzero(
                (dk[:, -1] - self.reach_max < best - 1e-9) & (best > 1e-7) & (best <= beyond_mm)
            )[0]
            if len(todo):
                lists = self.tree.query_ball_point(P[todo], best[todo] + self.reach_max)
                lens = np.array([len(x) for x in lists], dtype=np.int64)
                if lens.sum():
                    cand = np.concatenate([np.asarray(x, dtype=np.int64) for x in lists])
                    owner = np.repeat(todo, lens)
                    qq = _closest_on_triangles(self.tris[cand], P[owner])
                    d2 = np.linalg.norm(qq - P[owner], axis=1)
                    o = np.lexsort((cand, d2, owner))
                    first = o[np.r_[True, owner[o][1:] != owner[o][:-1]]]
                    w = owner[first]
                    better = d2[first] < best[w]
                    w, first = w[better], first[better]
                    best[w], bq[w], bi[w] = d2[first], qq[first], cand[first]
            dist[a : a + m], cpt[a : a + m], piece[a : a + m] = best, bq, bi
        return dist, cpt, self.nrm[piece], self.fid[piece]


# ------------------------------------------------------------------------------------- ICP
def _point_to_plane_step(p: np.ndarray, q: np.ndarray, n: np.ndarray) -> np.ndarray:
    c = p.mean(axis=0)
    pc = p - c
    A = np.hstack([np.cross(pc, n), n])
    b = -np.einsum("ij,ij->i", p - q, n)
    x, *_ = np.linalg.lstsq(A, b, rcond=None)
    w, t = x[:3], x[3:]
    ang = float(np.linalg.norm(w))
    R = np.eye(3) if ang < 1e-15 else _rotation(w / ang, ang)
    dT = np.eye(4)
    dT[:3, :3] = R
    dT[:3, 3] = c + t - R @ c
    return dT


def _rotation(axis: np.ndarray, ang: float) -> np.ndarray:
    x, y, z = axis
    K = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * (K @ K)


def rotation_delta(T: np.ndarray) -> tuple[float, float]:
    """(angle in degrees, translation norm in mm) of a rigid T."""
    c = (np.trace(T[:3, :3]) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))), float(np.linalg.norm(T[:3, 3]))


@dataclass
class IcpResult:
    T: np.ndarray
    converged: bool
    iterations: int
    rmse_mm: float
    kept_fraction: float
    history: list[dict] = field(default_factory=list)


def icp(
    src_pts: np.ndarray,
    target: Surface,
    *,
    max_iter: int = 50,
    trim_quantile: float = 0.9,
    trim_cap_mm: float = 2.0,
    min_cut_mm: float = 0.3,
    tol_move_mm: float = 1e-4,
) -> IcpResult:
    """Trimmed point-to-plane ICP of ``src_pts`` onto ``target`` (scanreg.icp, ported).

    Trimming: the first iteration keeps correspondences within ``min(trim_quantile distance,
    trim_cap_mm)``; later cutoffs only shrink, to 3x the median inlier distance, never below
    ``min_cut_mm``. Converged when no sample moves more than ``tol_move_mm`` in an iteration.
    """
    T = np.eye(4)
    hist: list[dict] = []
    converged, kept, rmse = False, 1.0, math.inf
    cut_prev: float | None = None
    for it in range(1, max_iter + 1):
        moved = apply(T, src_pts)
        d, cp, nrm, _ = target.query(moved, beyond_mm=trim_cap_mm)
        if cut_prev is None:
            cut = min(trim_cap_mm, float(np.quantile(d, trim_quantile)))
        else:
            inl = d[d <= cut_prev]
            cut = min(cut_prev, max(min_cut_mm, 3.0 * float(np.median(inl)) if len(inl) else 0))
        cut_prev = cut
        keep = d <= cut
        if keep.sum() < 10:
            break
        kept = float(keep.mean())
        rmse = float(np.sqrt(np.mean(d[keep] ** 2)))
        dT = _point_to_plane_step(moved[keep], cp[keep], nrm[keep])
        move = float(np.linalg.norm(apply(dT, moved[keep]) - moved[keep], axis=1).max())
        T = dT @ T
        hist.append({"it": it, "rmse": rmse, "kept": kept, "cut_mm": cut, "max_move_mm": move})
        if move < tol_move_mm:
            converged = True
            break
    return IcpResult(T, converged, len(hist), rmse, kept, hist)
