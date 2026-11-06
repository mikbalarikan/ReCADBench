"""B-rep access for the evaluator: STEP loading, face records, tessellation, rigid transforms,
topology counts.

The evaluator may use the pinned kernel (build123d / OCP) for B-rep operations; it never imports
backend adapter code. Everything here is deterministic: tessellation is serial (no parallel
meshing) and faces are visited in the kernel's stable explorer order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FACE_TYPES = ("plane", "cylinder", "cone", "sphere", "torus", "bspline")


# ------------------------------------------------------------------------------------ loading
def load_step(path: str | Path):
    """Load a STEP file as a build123d shape. Raises ``ValueError`` if nothing loads."""
    from build123d import import_step

    p = Path(path)
    if not p.is_file():
        raise ValueError(f"{p}: file not found")
    try:
        shape = import_step(str(p))
    except Exception as exc:  # noqa: BLE001 - any kernel failure is a load failure
        raise ValueError(f"{p.name}: STEP does not load ({exc})") from exc
    if shape is None or shape.wrapped is None or shape.wrapped.IsNull():
        raise ValueError(f"{p.name}: STEP is empty")
    return shape


def write_step(shape, path: str | Path) -> None:
    from build123d import export_step

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not export_step(shape, str(path)):
        raise RuntimeError(f"STEP export failed: {path}")


def solids(shape) -> list:
    return list(shape.solids())


def is_valid(shape) -> bool:
    from OCP.BRepCheck import BRepCheck_Analyzer

    return bool(BRepCheck_Analyzer(shape.wrapped).IsValid())


def shells_closed(shape) -> bool:
    """Every shell of the shape is closed (each edge bounds two faces; seams count twice)."""
    from OCP.BRep import BRep_Tool

    shells = list(shape.shells())
    return bool(shells) and all(BRep_Tool.IsClosed_s(s.wrapped) for s in shells)


def volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, g)
    return float(g.Mass())


def bbox(shape) -> tuple[np.ndarray, np.ndarray]:
    bb = shape.bounding_box()
    return (
        np.array([bb.min.X, bb.min.Y, bb.min.Z], dtype=float),
        np.array([bb.max.X, bb.max.Y, bb.max.Z], dtype=float),
    )


# ---------------------------------------------------------------------------------- transforms
def transform(shape, T: np.ndarray):
    """Copy of ``shape`` moved by the rigid 4x4 ``T`` (x' = R x + t)."""
    from build123d import Shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    T = np.asarray(T, dtype=float)
    tr = gp_Trsf()
    tr.SetValues(*[float(T[i, j]) for i in range(3) for j in range(4)])
    out = BRepBuilderAPI_Transform(shape.wrapped, tr, True).Shape()
    return Shape.cast(out)


def apply(T: np.ndarray, pts: np.ndarray) -> np.ndarray:
    T = np.asarray(T, dtype=float)
    return np.asarray(pts, dtype=float) @ T[:3, :3].T + T[:3, 3]


# ------------------------------------------------------------------------------- face records
@dataclass
class FaceRec:
    """Analytic description of one face (all vectors in the shape's frame)."""

    index: int
    kind: str  # one of FACE_TYPES ('bspline' also stands for any other free-form surface)
    area: float
    face: object = field(repr=False)  # build123d Face
    normal: np.ndarray | None = None  # plane: outward unit normal
    origin: np.ndarray | None = None  # plane: a point; axis types: a point on the axis
    axis: np.ndarray | None = None  # cylinder / cone / torus: unit axis direction
    center: np.ndarray | None = None  # sphere / torus centre
    radius: float | None = None  # cylinder / sphere radius, torus major radius
    minor_radius: float | None = None  # torus
    half_angle: float | None = None  # cone semi-angle (rad)
    ref_radius: float | None = None  # cone radius at ``origin``
    reversed: bool = False  # face orientation is reversed w.r.t. its surface
    full_turn: bool = False  # periodic face spanning the whole u range


def _vec(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], dtype=float)


def face_records(shape) -> list[FaceRec]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_Torus,
    )
    from OCP.TopAbs import TopAbs_REVERSED

    recs: list[FaceRec] = []
    for i, f in enumerate(shape.faces()):
        ad = BRepAdaptor_Surface(f.wrapped)
        st = ad.GetType()
        rev = f.wrapped.Orientation() == TopAbs_REVERSED
        rec = FaceRec(index=i, kind="bspline", area=float(f.area), face=f, reversed=rev)
        u0, u1 = ad.FirstUParameter(), ad.LastUParameter()
        rec.full_turn = bool(ad.IsUPeriodic() and (u1 - u0) >= 2 * math.pi - 1e-6)
        if st == GeomAbs_Plane:
            pl = ad.Plane()
            u, v = 0.5 * (u0 + u1), 0.5 * (ad.FirstVParameter() + ad.LastVParameter())
            rec.kind, rec.origin = "plane", _vec(pl.Location())
            rec.normal = _face_normal_uv(f, u, v)
        elif st == GeomAbs_Cylinder:
            c = ad.Cylinder()
            rec.kind, rec.radius = "cylinder", float(c.Radius())
            rec.axis, rec.origin = _vec(c.Axis().Direction()), _vec(c.Location())
        elif st == GeomAbs_Cone:
            c = ad.Cone()
            rec.kind, rec.half_angle = "cone", float(c.SemiAngle())
            rec.axis, rec.origin = _vec(c.Axis().Direction()), _vec(c.Location())
            rec.ref_radius = float(c.RefRadius())
        elif st == GeomAbs_Sphere:
            s = ad.Sphere()
            rec.kind, rec.radius, rec.center = "sphere", float(s.Radius()), _vec(s.Location())
            rec.origin = rec.center
        elif st == GeomAbs_Torus:
            t = ad.Torus()
            rec.kind, rec.radius, rec.minor_radius = "torus", float(t.MajorRadius()), float(
                t.MinorRadius()
            )
            rec.axis, rec.center = _vec(t.Axis().Direction()), _vec(t.Location())
            rec.origin = rec.center
        recs.append(rec)
    return recs


def _face_normal_uv(face, u: float, v: float) -> np.ndarray:
    """Outward unit normal of a face at (u, v), honouring the face orientation."""
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Pnt, gp_Vec

    p, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face.wrapped).Normal(u, v, p, n)
    out = np.array([n.X(), n.Y(), n.Z()], dtype=float)
    ln = float(np.linalg.norm(out))
    return out / ln if ln > 0 else out


def outward_normal(rec: FaceRec, point: np.ndarray) -> np.ndarray:
    """Outward unit normal of the face at the surface point closest to ``point``."""
    from OCP.BRep import BRep_Tool
    from OCP.gp import gp_Pnt
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface

    surf = BRep_Tool.Surface_s(rec.face.wrapped)
    uv = ShapeAnalysis_Surface(surf).ValueOfUV(gp_Pnt(*[float(x) for x in point]), 1e-7)
    return _face_normal_uv(rec.face, uv.X(), uv.Y())


def face_type_areas(recs: list[FaceRec]) -> dict[str, float]:
    """Area fraction per face type over ``FACE_TYPES`` (free-form of any kind -> bspline)."""
    tot = sum(r.area for r in recs) or 1.0
    out = dict.fromkeys(FACE_TYPES, 0.0)
    for r in recs:
        out[r.kind] += r.area / tot
    return out


def point_face_distance(point: np.ndarray, rec: FaceRec) -> tuple[float, np.ndarray]:
    """Exact distance from ``point`` to the bounded face and the closest point on it."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(x) for x in point])).Vertex()
    ext = BRepExtrema_DistShapeShape(v, rec.face.wrapped)
    if not ext.IsDone() or ext.NbSolution() == 0:
        return math.inf, np.asarray(point, dtype=float)
    return float(ext.Value()), _vec(ext.PointOnShape2(1))


# -------------------------------------------------------------------------------- tessellation
@dataclass
class Tess:
    vertices: np.ndarray  # (n, 3)
    triangles: np.ndarray  # (m, 3) int, outward winding
    face_id: np.ndarray  # (m,) index into face_records order

    @property
    def tri_xyz(self) -> np.ndarray:
        return self.vertices[self.triangles]


def tessellate(shape, chord_mm: float, angular_rad: float = 0.25) -> Tess:
    """Per-face triangulation with face ids (serial meshing, deterministic)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location

    BRepMesh_IncrementalMesh(shape.wrapped, float(chord_mm), False, float(angular_rad), False)
    verts, tris, fids = [], [], []
    off = 0
    for i, f in enumerate(shape.faces()):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f.wrapped, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        nn = tri.NbNodes()
        v = np.empty((nn, 3))
        for k in range(1, nn + 1):
            p = tri.Node(k).Transformed(trsf)
            v[k - 1] = (p.X(), p.Y(), p.Z())
        nt = tri.NbTriangles()
        t = np.empty((nt, 3), dtype=np.int64)
        for k in range(1, nt + 1):
            a, b, c = tri.Triangle(k).Get()
            t[k - 1] = (a - 1, b - 1, c - 1)
        if f.wrapped.Orientation() == TopAbs_REVERSED:
            t = t[:, [0, 2, 1]]
        verts.append(v)
        tris.append(t + off)
        fids.append(np.full(nt, i, dtype=np.int64))
        off += nn
    if not tris:
        raise ValueError("shape has no triangulation")
    return Tess(np.vstack(verts), np.vstack(tris), np.concatenate(fids))


# ------------------------------------------------------------------------------------ topology
def genus(shape) -> int:
    """First Betti number b1 of a solid with one closed shell (= genus of its boundary).

    Euler characteristic of the boundary from B-rep topology: every face is a cell complex
    whose interior has chi = 2 - (number of wires) (a disk = 1 wire; seams keep periodic faces
    disk-like), so chi = V - E + sum_f (2 - w_f) and genus = (2 - chi) / 2.
    """
    v = len(shape.vertices())
    e = len(shape.edges())
    chi = v - e + sum(2 - len(f.wires()) for f in shape.faces())
    return int(round((2 - chi) / 2))


def triangle_face_count(recs: list[FaceRec]) -> int:
    """Planar faces bounded by exactly three edges (faceted / mesh-converted B-rep signal)."""
    n = 0
    for r in recs:
        if r.kind == "plane" and len(r.face.wires()) == 1 and len(r.face.edges()) == 3:
            n += 1
    return n
