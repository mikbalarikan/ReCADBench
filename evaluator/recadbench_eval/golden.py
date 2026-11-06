"""Golden candidates for evaluator tests: good / lego / faceted / dumb_solid / wrong_dim /
snap_everything (03_SCORING; PROMPTS_v2 H5).

Candidates are built **at test time into a temporary run dir** from a GT part (synthetic in CI,
an R385 pilot via ``RECADBENCH_GT_DIR`` on the evaluator host). They are derived from GT and must
never be committed. Each is written like a real prediction: ``out/output.step`` in the **scanner
frame** (``inv(T_scan_to_gt)`` applied) + ``out/features.json`` (+ ``run_manifest.json``).

| Candidate | Geometry | features.json | Expected |
|---|---|---|---|
| good | gt.step | synthesised from gt_plan (taxonomy counting rule) | PASS |
| wrong_dim | one key dim off by 1.5 x class tolerance | as good | lower S3 |
| snap_everything | movable key dims snapped to the 0.5 mm grid | as good | S3 drops |
| lego | gt.step | 4 x n_ref primitive boxes | S2 = 0, total capped at 40 |
| faceted | tessellated gt.step (one planar face per triangle) | as good | gate fail |
| dumb_solid | gt.step | ``unmapped`` non-empty, count 0 | gate fail |

Geometry edits move one face along its outward normal with a boolean: a slab / ring / offset
skin between the face and its moved copy is fused (material added) or cut (removed), then
same-domain faces are unified.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from . import brep
from .config import CLASS_KEYDIM_TOL, EvalConfig
from .intent import dim_handle, measure_dim

KINDS = ("good", "wrong_dim", "snap_everything", "lego", "faceted", "dumb_solid")
SNAP_GRID = 0.5


# ------------------------------------------------------------------------------ face moving
def _thick(face, delta: float):
    from build123d import Solid
    from OCP.BRepLib import BRepLib
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    mk = BRepOffsetAPI_MakeThickSolid()
    mk.MakeThickSolidBySimple(face.wrapped, float(delta))
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError("thick solid failed")
    ex = TopExp_Explorer(mk.Shape(), TopAbs_SOLID)
    sol = TopoDS.Solid(ex.Current())
    BRepLib.OrientClosedSolid_s(sol)
    return Solid(sol)


def _prism(face, vec: np.ndarray):
    from build123d import Solid
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    return Solid(BRepPrimAPI_MakePrism(face.wrapped, gp_Vec(*[float(x) for x in vec])).Shape())


def _ring(rec: brep.FaceRec, r_a: float, r_b: float):
    """Coaxial tube between radii r_a, r_b over the face's axial (v) range."""
    from build123d import Cylinder, Location, Plane
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    ad = BRepAdaptor_Surface(rec.face.wrapped)
    v0, v1 = ad.FirstVParameter(), ad.LastVParameter()
    base = rec.origin + v0 * rec.axis
    h = v1 - v0
    pl = Plane(origin=tuple(base), z_dir=tuple(rec.axis))
    lo, hi = sorted((r_a, r_b))
    outer = Cylinder(hi, h, align=None)
    inner = Cylinder(lo, h, align=None)
    return Location(pl) * (outer - inner)


def _tool(rec: brep.FaceRec, delta: float):
    if rec.kind == "plane":
        return _prism(rec.face, rec.normal * delta)
    if rec.kind == "cylinder" and rec.full_turn:
        c = rec.face.center()
        p = np.array([c.X, c.Y, c.Z], dtype=float)
        n = brep.outward_normal(rec, p)
        v = p - rec.origin
        radial = v - np.dot(v, rec.axis) * rec.axis
        sgn = 1.0 if float(np.dot(n, radial)) > 0 else -1.0
        return _ring(rec, rec.radius, rec.radius + sgn * delta)
    return _thick(rec.face, delta)


def move_faces(shape, recs: list[brep.FaceRec], delta: float):
    """Move faces by ``delta`` mm along their outward normals (delta > 0 adds material)."""
    if abs(delta) < 1e-12 or not recs:
        return shape
    tools = [_tool(r, delta) for r in recs]
    expect = sum(r.area for r in recs) * abs(delta)
    v0 = brep.volume(shape)
    out = shape.fuse(*tools) if delta > 0 else shape.cut(*tools)
    out = out.clean()
    sols = brep.solids(out)
    if len(sols) != 1:
        raise RuntimeError(f"face move produced {len(sols)} solids")
    out = sols[0]
    dv = brep.volume(out) - v0
    if math.copysign(1.0, dv) != math.copysign(1.0, delta) or not (
        0.3 * expect <= abs(dv) <= 3.0 * expect + 1e-6
    ):
        raise RuntimeError(f"face move: volume change {dv:.5f} vs expected ~{expect:.5f}")
    return out


def move_face(shape, rec: brep.FaceRec, delta: float):
    return move_faces(shape, [rec], delta)


def _same_surface(recs: list[brep.FaceRec], rec: brep.FaceRec) -> list[brep.FaceRec]:
    """``rec`` plus the other faces of the same cylinder / sphere (a hole split in halves)."""
    if rec.kind not in ("cylinder", "sphere"):
        return [rec]
    out = []
    for r in recs:
        if r.kind != rec.kind or abs(r.radius - rec.radius) > 1e-6:
            continue
        if rec.kind == "sphere":
            same = np.linalg.norm(r.center - rec.center) < 1e-4
        else:
            same = abs(abs(float(np.dot(r.axis, rec.axis))) - 1.0) < 1e-8 and float(
                np.linalg.norm(np.cross(r.origin - rec.origin, rec.axis))) < 1e-4
        if same:
            out.append(r)
    return out or [rec]


def set_dim(shape, dim: dict, target: float, cfg: EvalConfig):
    """Move the face that drives ``dim`` so it measures ``target``. Returns (shape, achieved)."""
    recs = brep.face_records(shape)
    cur, _ = measure_dim(dim, recs, cfg)
    h = dim_handle(dim, recs, cfg)
    if cur is None or h is None:
        raise ValueError(f"{dim['name']}: dim cannot be moved by one face")
    rec, _, k = h
    group = _same_surface(recs, rec) if dim["kind"] in ("diameter", "radius") else [rec]
    shape = move_faces(shape, group, k * (target - cur))
    got, _ = measure_dim(dim, brep.face_records(shape), cfg)
    return shape, got


def snap(value: float, grid: float = SNAP_GRID) -> float:
    return round(round(value / grid) * grid, 6)


# ---------------------------------------------------------------------------------- faceted
def faceted_solid(shape, chord_mm: float):
    """One planar face per triangle of the tessellation, sewn into a solid."""
    from build123d import Solid
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeSolid,
        BRepBuilderAPI_Sewing,
    )
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    t = brep.tessellate(shape, chord_mm, 0.5)
    # weld by exact coordinates so neighbouring faces share vertices
    v, inv = np.unique(np.round(t.vertices, 5), axis=0, return_inverse=True)
    tris = inv.reshape(-1)[t.triangles]
    sew = BRepBuilderAPI_Sewing(1e-4)
    for a, b, c in tris:
        if a == b or b == c or a == c:
            continue
        poly = BRepBuilderAPI_MakePolygon(gp_Pnt(*v[a]), gp_Pnt(*v[b]), gp_Pnt(*v[c]), True)
        f = BRepBuilderAPI_MakeFace(poly.Wire(), True)
        if f.IsDone():
            sew.Add(f.Face())
    sew.Perform()
    ex = TopExp_Explorer(sew.SewedShape(), TopAbs_SHELL)
    shell = TopoDS.Shell(ex.Current())
    mk = BRepBuilderAPI_MakeSolid(shell)
    from OCP.BRepLib import BRepLib
    from OCP.ShapeFix import ShapeFix_Solid

    fix = ShapeFix_Solid(mk.Solid())
    fix.Perform()
    sol = TopoDS.Solid(fix.Solid())
    BRepLib.OrientClosedSolid_s(sol)
    return Solid(sol)


# ------------------------------------------------------------------------------ features.json
def features_from_plan(gt_plan: dict, face_count: int, replace: dict[float, float] | None = None
                       ) -> dict:
    """A features.json an adapter would extract from a model built exactly as ``gt_plan``.

    Every IR feature becomes one native feature of its taxonomy type; ``counted`` follows the
    taxonomy counting rule; params are the resolved scalar values; ``parameters`` are the IR
    parameters. ``replace`` maps old -> new values (edited candidates)."""
    from recadbench_spec import load_taxonomy

    tax = load_taxonomy()
    rep = replace or {}

    def fix(x: float) -> float:
        for old, new in rep.items():
            if abs(x - old) < 1e-9:
                return new
        return x

    params = {p["name"]: float(p["value"]) for p in gt_plan.get("parameters") or []}
    feats = []
    for f in gt_plan.get("features") or []:
        sp = {}
        for k, v in (f.get("params") or {}).items():
            if isinstance(v, dict) and "ref" in v:
                sp[k] = fix(params[v["ref"]])
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                sp[k] = fix(float(v))
        feats.append({"native_name": f["id"], "native_type": f["type"],
                      "taxonomy_type": f["type"], "counted": tax.is_counted(f["type"]),
                      "params": sp})
    return {
        "tool": "golden",
        "native_file": "golden",
        "count": sum(1 for f in feats if f["counted"]),
        "features": feats,
        "unmapped": [],
        "face_count": int(face_count),
        "parameters": [{"name": n, "value": fix(v)} for n, v in params.items()],
    }


def lego_features(n_boxes: int, face_count: int) -> dict:
    feats = [{"native_name": f"box_{i:03d}", "native_type": "Box", "taxonomy_type": "primitive",
              "counted": True, "params": {"size": [1.0, 1.0, 1.0]}} for i in range(n_boxes)]
    return {"tool": "golden", "native_file": "golden", "count": n_boxes, "features": feats,
            "unmapped": [], "face_count": int(face_count), "parameters": []}


def dumb_features(face_count: int) -> dict:
    return {"tool": "golden", "native_file": "golden", "count": 0, "features": [],
            "unmapped": ["Solid (raw kernel shape, no feature history)"],
            "face_count": int(face_count)}


# -------------------------------------------------------------------------------- run dirs
def manifest(run_id: str, part_id: str) -> dict:
    return {
        "run_id": run_id, "track": "E", "part_id": part_id, "split": "dev", "replicate": 0,
        "strategy": {"id": "oracle", "git_sha": "golden"},
        "model": {"id": "golden", "provider": "none"},
        "spec_version": "0.3.0", "budgets": {}, "harness_level": "H1",
        "backend": {"id": "build123d", "software_version": "golden"},
        "skillset": {"id": "build123d@v1", "git_sha": "golden"},
    }


def _write(run_dir: Path, part_id: str, kind: str, shape_gt, T_scan_to_gt: np.ndarray,
           feats: dict) -> Path:
    out = run_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    shape_scan = brep.transform(shape_gt, np.linalg.inv(T_scan_to_gt))
    brep.write_step(shape_scan, out / "output.step")
    (out / "features.json").write_text(json.dumps(feats, indent=2) + "\n", encoding="utf-8",
                                       newline="\n")
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest(f"golden_{part_id}_{kind}", part_id), indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return run_dir


def build(kind: str, run_dir: str | Path, *, part_id: str, gt_shape, gt_plan: dict,
          key_dims: dict, meta: dict, T_scan_to_gt: np.ndarray,
          cfg: EvalConfig | None = None) -> dict:
    """Write candidate ``kind`` into ``run_dir``. Returns a description of what was changed."""
    cfg = cfg or EvalConfig()
    run_dir = Path(run_dir)
    tol = CLASS_KEYDIM_TOL[meta["tolerance_class"]]
    nfaces = len(gt_shape.faces())
    info: dict = {"kind": kind, "changed": []}
    shape = gt_shape
    if kind == "good":
        feats = features_from_plan(gt_plan, nfaces)
    elif kind == "lego":
        feats = lego_features(4 * int(meta["n_ref"]), nfaces)
    elif kind == "dumb_solid":
        feats = dumb_features(nfaces)
    elif kind == "faceted":
        shape = faceted_solid(gt_shape, chord_mm=0.05)
        feats = features_from_plan(gt_plan, len(shape.faces()))
    elif kind == "wrong_dim":
        dims = key_dims.get("dims") or []
        order = sorted(dims, key=lambda d: (d["kind"] != "diameter", d["name"]))
        last = None
        for d in order:
            target = float(d["nominal"]) - 1.5 * tol
            try:
                shape, got = set_dim(gt_shape, d, target, cfg)
            except (ValueError, RuntimeError) as exc:
                last = exc
                continue
            if got is None or abs(got - target) > 1e-3:
                last = RuntimeError(f"{d['name']}: measured {got} after the edit, wanted {target}")
                shape = gt_shape
                continue
            info["changed"].append({"dim": d["name"], "nominal": d["nominal"], "set": got})
            feats = features_from_plan(gt_plan, len(shape.faces()), {float(d["nominal"]): target})
            break
        else:
            raise RuntimeError(f"wrong_dim: no key dim could be moved ({last})")
    elif kind == "snap_everything":
        rep = {}
        for d in key_dims.get("dims") or []:
            target = snap(float(d["nominal"]))
            cur, _ = measure_dim(d, brep.face_records(shape), cfg)
            if cur is not None and abs(target - cur) < 1e-6:
                continue  # already standard (an earlier edit may have moved it: re-measured)
            try:
                shape, got = set_dim(shape, d, target, cfg)
            except (ValueError, RuntimeError) as exc:
                info["changed"].append({"dim": d["name"], "skipped": str(exc)})
                continue
            rep[float(d["nominal"])] = target
            info["changed"].append({"dim": d["name"], "nominal": d["nominal"], "set": got})
        feats = features_from_plan(gt_plan, len(shape.faces()), rep)
    else:
        raise ValueError(f"unknown candidate kind {kind!r}")
    _write(run_dir, part_id, kind, shape, T_scan_to_gt, feats)
    return info
