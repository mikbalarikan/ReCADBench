"""S3 design intent (20): key dims (12), standard values (4), relations (4). 03_SCORING §3.

Key dims are measured **intrinsically** on the aligned candidate B-rep (radii, plane-to-plane,
axis-to-axis distances), so residual alignment error does not count against the agent. Each
dim's faces are found as the nearest face of the same type to the GT ``near_point``, with the
axis within ``axis_tol_deg`` of ``axis_dir`` when given, and no farther than ``max_dist`` (a
missing feature is not measured on a duplicate somewhere else on the part).

Points without GT standard dims (``key_dims.standard: true``): the 4 standard-value points then
follow the key-dim fraction (the credit is neither free nor lost); likewise the 4 relation points
when the GT has no coaxial group / symmetry plane. Snapping a non-standard GT dim to a standard
value fails that dim's tolerance, so "snap everything" loses key-dim credit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .brep import FaceRec, outward_normal, point_face_distance
from .config import EvalConfig
from .geom import Surface

AXIS_KINDS = ("cylinder", "cone", "torus")


@dataclass
class DimResult:
    name: str
    kind: str
    nominal: float
    measured: float | None
    error: float | None
    ok: bool
    standard: bool
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "nominal": self.nominal,
            "measured": self.measured,
            "error": self.error,
            "ok": self.ok,
            "standard": self.standard,
            "note": self.note,
        }


# ------------------------------------------------------------------------------ face finding
def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _axis_ok(rec: FaceRec, axis_dir, tol_deg: float) -> bool:
    if axis_dir is None:
        return True
    a = rec.axis if rec.axis is not None else rec.normal
    if a is None:
        return True  # spheres have no axis
    c = abs(float(np.dot(_unit(a), _unit(axis_dir))))
    return c >= math.cos(math.radians(tol_deg))


def find_face(
    recs: list[FaceRec], point, face_type: str | None, axis_dir=None, tol_deg: float = 2.0,
    max_dist: float | None = None,
) -> tuple[FaceRec | None, float, np.ndarray | None]:
    """Nearest face of ``face_type`` (axis within ``tol_deg``) to ``point``; (rec, dist, foot).

    With ``max_dist``, a nearest face farther than that is no match: ``(None, dist, None)``."""
    p = np.asarray(point, dtype=float)
    best: tuple[FaceRec | None, float, np.ndarray | None] = (None, math.inf, None)
    for r in recs:
        if face_type and r.kind != face_type:
            continue
        if not _axis_ok(r, axis_dir, tol_deg):
            continue
        d, foot = point_face_distance(p, r)
        if d < best[1] - 1e-12:
            best = (r, d, foot)
    if max_dist is not None and best[0] is not None and best[1] > max_dist:
        return None, best[1], None
    return best


def _missing(ftype, dist: float, what: str = "") -> str:
    if math.isfinite(dist):
        return f"no {what}{ftype} face near near_point (nearest at {dist:.3f} mm)"
    return f"no {what}{ftype} face found"


def _radius_at(rec: FaceRec, foot: np.ndarray) -> float:
    if rec.kind in ("cylinder", "sphere"):
        return float(rec.radius)
    if rec.kind == "torus":
        return float(rec.minor_radius)
    if rec.kind == "cone":
        v = foot - rec.origin
        return float(np.linalg.norm(v - np.dot(v, rec.axis) * rec.axis))
    raise ValueError(f"no radius on a {rec.kind} face")


def _line_distance(o1, d1, o2) -> float:
    return float(np.linalg.norm(np.cross(np.asarray(o2) - np.asarray(o1), _unit(d1))))


def _same_axis(a: FaceRec, b: FaceRec, tol_mm: float, tol_deg: float) -> bool:
    if a.axis is None or b.axis is None:
        return False
    if abs(float(np.dot(a.axis, b.axis))) < math.cos(math.radians(tol_deg)):
        return False
    return _line_distance(a.origin, a.axis, b.origin) <= tol_mm


def measure_dim(dim: dict, recs: list[FaceRec], cfg: EvalConfig,
                max_dist: float | None = None) -> tuple[float | None, str]:
    """Measure one key dim on a candidate (GT frame). Returns (value or None, note)."""
    m = dim.get("measure") or {}
    kind = dim["kind"]
    ftype = m.get("face_type")
    a, da, pa = find_face(recs, m["near_point"], ftype, m.get("axis_dir"), cfg.axis_tol_deg,
                          max_dist)
    if a is None:
        return None, _missing(ftype, da)
    if kind in ("diameter", "radius"):
        r = _radius_at(a, pa)
        return (2.0 * r if kind == "diameter" else r), f"face {a.index} at {da:.3f} mm"
    sec = m.get("second") or {}
    if "near_point" not in sec:
        return None, "second.near_point missing"
    stype = sec.get("face_type", ftype if kind not in ("axis_to_face",) else "plane")
    if kind == "axis_distance":
        stype = sec.get("face_type", ftype)
    b, db, pb = find_face(recs, sec["near_point"], stype, sec.get("axis_dir", m.get("axis_dir"))
                          if kind == "axis_distance" else None, cfg.axis_tol_deg, max_dist)
    if b is None:
        return None, _missing(stype, db, "second ")
    note = f"faces {a.index}/{b.index} at {da:.3f}/{db:.3f} mm"
    if b.index == a.index and kind != "angle":
        return None, note + " (both ends on the same face)"
    if kind == "axis_distance":
        if a.axis is None or b.axis is None:
            return None, note + " (needs two axis faces)"
        return _line_distance(a.origin, a.axis, b.origin), note
    if kind == "axis_to_face":
        if a.axis is None:
            return None, note + " (first face has no axis)"
        nb = outward_normal(b, pb)
        return abs(float(np.dot(a.origin - pb, nb))), note
    if kind == "angle":
        va = a.axis if a.axis is not None else outward_normal(a, pa)
        vb = b.axis if b.axis is not None else outward_normal(b, pb)
        c = abs(float(np.dot(_unit(va), _unit(vb))))
        return math.degrees(math.acos(min(1.0, c))), note
    # face_distance / depth
    if a.kind == b.kind == "sphere" and np.linalg.norm(a.center - b.center) < 0.05:
        return abs(a.radius - b.radius), note + " (concentric spheres)"
    if a.kind == b.kind == "cylinder" and _same_axis(a, b, 0.05, cfg.axis_tol_deg):
        return abs(a.radius - b.radius), note + " (coaxial cylinders)"
    na, nb = outward_normal(a, pa), outward_normal(b, pb)
    d1 = abs(float(np.dot(pb - pa, na)))
    d2 = abs(float(np.dot(pa - pb, nb)))
    return 0.5 * (d1 + d2), note


def measure_key_dims(key_dims: dict, recs: list[FaceRec], tol: float, cfg: EvalConfig,
                     max_dist: float | None = None) -> list[DimResult]:
    out = []
    for dim in key_dims.get("dims") or []:
        val, note = measure_dim(dim, recs, cfg, max_dist)
        nom = float(dim["nominal"])
        err = None if val is None else val - nom
        # within the class tolerance, limits included (ISO reading of +-tol)
        ok = err is not None and abs(err) <= tol + 1e-9
        out.append(DimResult(dim["name"], dim["kind"], nom, val, err, ok,
                             bool(dim.get("standard", False)), note))
    return out


# ---------------------------------------------------------------------------------- relations
def coaxial_groups(recs: list[FaceRec], cfg: EvalConfig) -> list[list[FaceRec]]:
    """GT groups of >= 2 axis faces sharing one axis line, largest total area first."""
    axis_faces = [r for r in recs if r.kind in AXIS_KINDS and r.axis is not None]
    groups: list[list[FaceRec]] = []
    for r in axis_faces:
        for g in groups:
            if _same_axis(g[0], r, cfg.coaxial_tol_mm, cfg.axis_tol_deg):
                g.append(r)
                break
        else:
            groups.append([r])
    groups = [g for g in groups if len(g) >= 2]
    groups.sort(key=lambda g: (-sum(r.area for r in g), g[0].index))
    return groups


def _face_probe(rec: FaceRec) -> np.ndarray:
    c = rec.face.center()
    return np.array([c.X, c.Y, c.Z], dtype=float)


def symmetry_planes(gt_surface: Surface, gt_pts: np.ndarray, cfg: EvalConfig
                    ) -> list[tuple[np.ndarray, np.ndarray]]:
    """GT-frame planes (point, normal) the GT is mirror-symmetric about.

    Candidates: the three coordinate planes of the GT frame (a design frame) through the origin
    and through the bounding-box centre. Symmetric if the mirrored GT samples lie on the GT
    surface (p95 <= ``symmetry_gt_tol_mm``)."""
    out: list[tuple[np.ndarray, np.ndarray]] = []
    lo, hi = gt_pts.min(axis=0), gt_pts.max(axis=0)
    centre = 0.5 * (lo + hi)
    probe = gt_pts[:: max(1, len(gt_pts) // 4000)]
    for axis in range(3):
        n = np.zeros(3)
        n[axis] = 1.0
        for origin in (np.zeros(3), centre):
            if any(abs(float(np.dot(o - origin, n))) < 1e-6 and np.allclose(nn, n)
                   for o, nn in out):
                continue
            mir = probe - 2.0 * ((probe - origin) @ n)[:, None] * n
            d = gt_surface.query(mir, beyond_mm=1.0)[0]
            if float(np.quantile(d, 0.95)) <= cfg.symmetry_gt_tol_mm:
                out.append((origin.copy(), n))
                break
    return out


def relations(gt_recs: list[FaceRec], pred_recs: list[FaceRec], gt_surface: Surface,
              gt_pts: np.ndarray, pred_surface: Surface, pred_pts: np.ndarray, band: float,
              cfg: EvalConfig, max_dist: float | None = None) -> list[dict]:
    """GT-coaxial groups stay coaxial (< coaxial_tol_mm), GT symmetry planes stay symmetric
    (mirrored candidate samples within ``band`` at p95). With ``max_dist``, a candidate face
    only matches a GT face if it lies within ``max_dist`` of where the GT face is (measured
    from the same probe point, which for a full cylinder is on its axis)."""
    rel: list[dict] = []
    for g in coaxial_groups(gt_recs, cfg)[: cfg.max_relations]:
        matched = []
        for r in g:
            probe = _face_probe(r)
            lim = None
            if max_dist is not None:
                lim = point_face_distance(probe, r)[0] + max_dist
            m, _, _ = find_face(pred_recs, probe, r.kind, r.axis, cfg.axis_tol_deg, lim)
            if m is not None and m.axis is not None:
                matched.append(m)
        ok = len(matched) == len(g) and all(
            _same_axis(matched[0], m, cfg.coaxial_tol_mm, cfg.axis_tol_deg) for m in matched[1:]
        )
        rel.append({"kind": "coaxial", "gt_faces": [r.index for r in g], "ok": bool(ok)})
    for origin, n in symmetry_planes(gt_surface, gt_pts, cfg):
        probe = pred_pts[:: max(1, len(pred_pts) // 4000)]
        mir = probe - 2.0 * ((probe - origin) @ n)[:, None] * n
        d = pred_surface.query(mir, beyond_mm=2.0)[0]
        p95 = float(np.quantile(d, 0.95))
        rel.append({"kind": "symmetry", "normal": n.tolist(), "origin": origin.tolist(),
                    "p95_mm": p95, "ok": bool(p95 <= band)})
    return rel


# ------------------------------------------------------------------------------------- score
def score_s3(dims: list[DimResult], rels: list[dict]) -> tuple[float, dict]:
    n = len(dims)
    frac = sum(d.ok for d in dims) / n if n else 0.0
    std = [d for d in dims if d.standard]
    std_frac = sum(d.ok for d in std) / len(std) if std else frac
    rel_frac = sum(r["ok"] for r in rels) / len(rels) if rels else frac
    s3 = 12.0 * frac + 4.0 * std_frac + 4.0 * rel_frac
    return s3, {
        "key_dims_ok": frac,
        "standard_ok": std_frac,
        "standard_dims": len(std),
        "relations_ok": rel_frac,
        "relations": len(rels),
    }


# ------------------------------------------------------------------- dim -> face movement map
def dim_handle(dim: dict, recs: list[FaceRec], cfg: EvalConfig, max_dist: float | None = None
               ) -> tuple[FaceRec, np.ndarray, float] | None:
    """The face that changes a key dim and how: ``(face, point on it, k)`` such that moving the
    face by ``k * dx`` along its outward normal changes the dim by ``dx`` (positive outward
    offset = material added). None for dims that are not a single-face move (axis distances,
    angles). Used for S5 volume-direction checks and to build golden candidates."""
    m = dim.get("measure") or {}
    kind = dim["kind"]
    a, _, pa = find_face(recs, m["near_point"], m.get("face_type"), m.get("axis_dir"),
                         cfg.axis_tol_deg, max_dist)
    if a is None:
        return None
    if kind in ("diameter", "radius"):
        if a.kind not in ("cylinder", "sphere", "cone", "torus"):
            return None
        n = outward_normal(a, pa)
        if a.kind == "sphere":
            radial = pa - a.center
        elif a.kind == "torus":
            v = pa - a.center
            ring = v - np.dot(v, a.axis) * a.axis
            ring = a.radius * _unit(ring)
            radial = v - ring
        else:
            v = pa - a.origin
            radial = v - np.dot(v, a.axis) * a.axis
        convex = float(np.dot(n, radial)) > 0
        k = (0.5 if kind == "diameter" else 1.0) * (1.0 if convex else -1.0)
        return a, pa, k
    if kind in ("face_distance", "depth"):
        sec = m.get("second") or {}
        if "near_point" not in sec:
            return None
        b, _, pb = find_face(recs, sec["near_point"], sec.get("face_type", m.get("face_type")),
                             None, cfg.axis_tol_deg, max_dist)
        if b is None or b.index == a.index:
            return None
        nb = outward_normal(b, pb)
        s = float(np.dot(nb, pb - pa))
        if abs(s) < 1e-9:
            return None
        return b, pb, (1.0 if s > 0 else -1.0)
    return None
