"""Deterministic IR -> ``cad/part.py`` writer for the replay backend (build123d skillset contract).

This plays the part of an oracle agent with the build123d skillset, without a model: it turns
the seeded plan into one self-contained ``cad/part.py`` that follows the skillset rules (module-
level ``# IR parameter`` literals named after the plan, IR numbers kept as numbers, build123d
only, no file access, final ``part``). It is a test harness, not a reference executor: it covers
the constructs the R385 pilot HS01 plan and the synthetic CI plans use, and refuses the rest
(:class:`Unsupported`, reported per feature by the replay script).

Covered: ``extrude`` (new/add/cut/intersect; distance, direction vector, symmetric) and
``revolve`` of closed sketches (polygon, circle, rect, slot, line+arc loops), ``pattern_circular``
of extrude / revolve seeds (algebra placement copies = one pattern, skillset section 3),
``hole`` (simple / counterbore, through or depth, one or more positions; direction from the
``refs`` plane / datum, or the frame-mapping note of a mapped face selector), ``fillet`` /
``chamfer`` on edge sets ``<F>:edges:all|convex|concave`` (convexity by an inside test) and
face selectors, ``primitive`` box / cylinder.
"""

from __future__ import annotations

import re
from typing import Any

NOTE_DIR = re.compile(r"direction [+-][XYZ] of the reference frame is \[([^\]]+)\]")


class Unsupported(ValueError):
    pass


HELPERS = '''
def _sig(face):
    """Signature of the surface a face lies on (copied from lib/b3dhelpers)."""
    kind = face.geom_type
    if kind == GeomType.PLANE:
        n = face.normal_at()
        return ("PLANE", round(n.X, 4), round(n.Y, 4), round(n.Z, 4),
                round(n.dot(face.center()), 4))
    if kind in (GeomType.CYLINDER, GeomType.CONE, GeomType.TORUS):
        ax = face.axis_of_rotation
        if ax is not None:
            d = ax.direction
            if (d.X, d.Y, d.Z) < (0, 0, 0):
                d = -d
            p = ax.position - d * ax.position.dot(d)
            r = face.radius if kind == GeomType.CYLINDER else 0.0
            return (kind.name, round(d.X, 4), round(d.Y, 4), round(d.Z, 4), round(p.X, 4),
                    round(p.Y, 4), round(p.Z, 4), round(r or 0.0, 4))
    c = face.center()
    return (kind.name, round(c.X, 3), round(c.Y, 3), round(c.Z, 3))


def _sigs(shape):
    return set() if shape is None else {_sig(f) for f in shape.faces()}


def _edge_kind(shape, edge, eps=0.01):
    """convex / concave / smooth for an edge between two faces (inside test at the midpoint)."""
    p = edge.position_at(0.5)
    faces = [f for f in shape.faces() if any(e.is_same(edge) for e in f.edges())]
    if len(faces) != 2:
        return "smooth"
    n1, n2 = faces[0].normal_at(p), faces[1].normal_at(p)
    d = n1 - n2
    if d.length < 1e-3:
        return "smooth"
    d = d.normalized()
    a = shape.is_inside(p + d * eps)
    b = shape.is_inside(p - d * eps)
    if a and b:
        return "concave"
    if not a and not b:
        return "convex"
    return "smooth"


def _owned_faces(shape, owned, fid):
    return [f for f in shape.faces() if _sig(f) in owned.get(fid, set())]


def _edges(shape, owned, selector):
    parts = selector.split(":")
    found = {}
    if len(parts) == 3 and parts[1] == "edges":
        for f in _owned_faces(shape, owned, parts[0]):
            for e in f.edges():
                if parts[2] == "all" or _edge_kind(shape, e) == parts[2]:
                    c = e.position_at(0.5)
                    found[(round(c.X, 6), round(c.Y, 6), round(c.Z, 6), round(e.length, 6))] = e
    else:
        raise ValueError("unsupported selector " + selector)
    return [found[k] for k in sorted(found)]


def _rot(origin, axis, deg):
    loc = Location(Plane(origin=origin, z_dir=axis))
    return loc * Rot(0, 0, deg) * loc.inverse()


def _edge_op(op, shape, edges, **kw):
    """One fillet / chamfer call on the edges of the set that the kernel can round together
    (the plan's edge sets are coarse; edges that fail alone are left sharp and reported)."""
    try:
        return op(edges, **kw), []
    except Exception:
        pass
    keep, dropped = [], []
    for e in edges:
        try:
            op(keep + [e], **kw)
            keep.append(e)
        except Exception:
            dropped.append(e)
    if not keep:
        return shape, dropped
    return op(keep, **kw), dropped
'''


def _num(v: Any) -> str:
    if isinstance(v, dict) and set(v) == {"ref"}:
        return v["ref"]
    if isinstance(v, bool):
        raise Unsupported(f"boolean where a number is expected: {v}")
    return repr(float(v)) if isinstance(v, float) else repr(v)


def _vec(v) -> str:
    return "(" + ", ".join(repr(float(x)) for x in v) + ")"


class Writer:
    def __init__(self, plan: dict):
        self.plan = plan
        self.lines: list[str] = []
        self.frames: dict[str, dict] = {}
        for d in plan.get("datums") or []:
            self.frames[d["id"]] = {"kind": d["kind"], "origin": d["origin"],
                                    "normal": d["direction"], "x_dir": d.get("x_dir")}
        for p in plan.get("reference_planes") or []:
            self.frames[p["id"]] = {"kind": "plane", "origin": p["origin"], "normal": p["normal"],
                                    "x_dir": p["x_dir"]}
        self.feats = {f["id"]: f for f in plan["features"]}
        self.skipped: list[str] = []
        self.has_body = False

    def emit(self, s: str = "") -> None:
        self.lines.append(s)

    # frames ----------------------------------------------------------------------------------
    def plane(self, ref: str) -> str:
        fr = self.frames.get(ref)
        if fr is None or fr["kind"] != "plane":
            raise Unsupported(f"sketch plane {ref!r} is not a datum / reference plane")
        return (f"Plane(origin={_vec(fr['origin'])}, x_dir={_vec(fr['x_dir'])}, "
                f"z_dir={_vec(fr['normal'])})")

    def axis(self, val) -> tuple[str, str]:
        if isinstance(val, str):
            fr = self.frames.get(val)
            if fr is None:
                raise Unsupported(f"axis {val!r} is not a datum")
            return _vec(fr["origin"]), _vec(fr["normal"])
        return "(0.0, 0.0, 0.0)", _vec(val)

    # sketches --------------------------------------------------------------------------------
    def sketch(self, var: str, sk: dict) -> None:
        self.emit(f"with BuildSketch({self.plane(sk['plane'])}) as {var}:")
        ents = sk["entities"]
        loop = [e for e in ents if e["kind"] in ("line", "arc")]
        closed = [e for e in ents if e["kind"] not in ("line", "arc")]
        for e in closed:
            k = e["kind"]
            if k == "polygon":
                pts = ", ".join(f"({_num(p[0])}, {_num(p[1])})" for p in e["points"])
                self.emit(f"    Polygon({pts}, align=None)")
            elif k == "circle":
                c = e["center"]
                self.emit(f"    with Locations(({_num(c[0])}, {_num(c[1])})):")
                self.emit(f"        Circle({_num(e['radius'])})")
            elif k == "rect":
                c = e["center"]
                rot = _num(e.get("angle_deg", 0))
                self.emit(f"    with Locations(({_num(c[0])}, {_num(c[1])})):")
                self.emit(f"        Rectangle({_num(e['width'])}, {_num(e['height'])}, "
                          f"rotation={rot})")
            elif k == "slot":
                self.emit(f"    _c1, _c2 = Vector({_num(e['c1'][0])}, {_num(e['c1'][1])}), "
                          f"Vector({_num(e['c2'][0])}, {_num(e['c2'][1])})")
                self.emit("    _d = _c2 - _c1")
                self.emit("    with Locations(((_c1.X + _c2.X) / 2, (_c1.Y + _c2.Y) / 2)):")
                self.emit(f"        SlotCenterToCenter(_d.length, 2 * {_num(e['radius'])}, "
                          "rotation=math.degrees(math.atan2(_d.Y, _d.X)))")
            else:
                raise Unsupported(f"sketch entity {k}")
        if loop:
            self.emit("    with BuildLine():")
            for e in loop:
                if e["kind"] == "line":
                    p1, p2 = e["p1"], e["p2"]
                    self.emit(f"        Line(({_num(p1[0])}, {_num(p1[1])}), "
                              f"({_num(p2[0])}, {_num(p2[1])}))")
                else:
                    c = e["center"]
                    self.emit(f"        CenterArc(({_num(c[0])}, {_num(c[1])}), "
                              f"{_num(e['radius'])}, {_num(e['start_deg'])}, "
                              f"({_num(e['end_deg'])} - {_num(e['start_deg'])}) % 360)")
            self.emit("    make_face()")

    # features --------------------------------------------------------------------------------
    def combine(self, fid: str, op: str, var: str) -> None:
        if op == "new" or not self.has_body:
            self.emit(f"body = {var}")
            self.has_body = True
        elif op == "add":
            self.emit(f"body = body + {var}")
        elif op == "cut":
            self.emit(f"body = body - {var}")
        elif op == "intersect":
            self.emit(f"body = body & {var}")

    def feature(self, f: dict) -> None:
        fid, t, prm = f["id"], f["type"], f.get("params") or {}
        op = f.get("operation", "add")
        var = f"f_{fid}"
        self.emit(f"# {fid} {t} ({op})")
        self.emit("_before = _sigs(body)")
        if t in ("extrude", "revolve"):
            self.sketch(f"sk_{fid}", f["sketch"])
            if t == "extrude":
                args = f"sk_{fid}.sketch, amount={_num(prm['distance'])}"
                d = prm.get("direction")
                if isinstance(d, list):
                    args += f", dir={_vec(d)}"
                elif d is not None:
                    raise Unsupported("extrude direction selector")
                if prm.get("symmetric"):
                    args += ", both=True"
                if "taper_deg" in prm:
                    args += f", taper={_num(prm['taper_deg'])}"
                self.emit(f"{var} = extrude({args})")
            else:
                o, d = self.axis(prm["axis"])
                self.emit(f"{var} = revolve(sk_{fid}.sketch, axis=Axis({o}, {d}), "
                          f"revolution_arc={_num(prm.get('angle_deg', 360))})")
            self.combine(fid, op, var)
        elif t == "primitive":
            shape, size = prm["shape"], prm["size"]
            o = _vec(prm.get("center", [0, 0, 0]))
            ax = prm.get("axis", [0, 0, 1])
            if not isinstance(ax, list):
                raise Unsupported("primitive axis selector")
            loc = f"Location(Plane(origin={o}, z_dir={_vec(ax)}))"
            if shape == "box":
                self.emit(f"{var} = {loc} * Box({_num(size[0])}, {_num(size[1])}, "
                          f"{_num(size[2])})")
            elif shape == "cylinder":
                self.emit(f"{var} = {loc} * Cylinder({_num(size[0])} / 2, {_num(size[1])})")
            else:
                raise Unsupported(f"primitive {shape}")
            self.combine(fid, op, var)
        elif t == "pattern_circular":
            o, d = self.axis(prm["axis"])
            n = _num(prm["count"])
            step = (f"{_num(prm['pitch_deg'])}" if "pitch_deg" in prm
                    else f"{_num(prm.get('angle_deg', 360))} / {n}")
            for seed in prm["features"]:
                sf = self.feats[seed]
                if sf["type"] not in ("extrude", "revolve", "primitive"):
                    raise Unsupported(f"pattern of a {sf['type']} seed")
                sop = "-" if sf.get("operation") == "cut" else "+"
                self.emit(f"for _k in range(int({n})):")
                self.emit(f"    body = body {sop} _rot({o}, {d}, _k * {step}) * f_{seed}")
        elif t in ("fillet", "chamfer"):
            sels = prm["edges"] if isinstance(prm["edges"], list) else [prm["edges"]]
            self.emit(f"_e = [e for s in {sels!r} for e in _edges(body, owned, s)]")
            self.emit("if _e:")
            if t == "fillet":
                self.emit(f"    body, _drop = _edge_op(fillet, body, _e, "
                          f"radius={_num(prm['radius'])})")
            else:
                self.emit(f"    body, _drop = _edge_op(chamfer, body, _e, "
                          f"length={_num(prm['distance'])})")
            self.emit("    if _drop:")
            self.emit(f"        sharp[{fid!r}] = len(_drop)")
            self.emit("else:")
            self.emit(f"    skipped.append({fid!r})")
        elif t == "hole":
            self.hole(f)
        else:
            raise Unsupported(f"feature type {t}")
        self.emit(f"owned[{fid!r}] = _sigs(body) - _before")
        if t == "pattern_circular":  # instances count as their seeds' surfaces too
            for seed in prm["features"]:
                self.emit(f"owned[{fid!r}] |= owned.get({seed!r}, set())")
        self.emit()

    def hole_dir(self, f: dict) -> str:
        """Outward normal of the entry face (the hole drills against it)."""
        for r in f.get("refs") or []:
            fr = self.frames.get(r)
            if fr is not None and fr["kind"] == "plane":
                return _vec(fr["normal"])
        m = NOTE_DIR.search((f.get("evidence") or {}).get("note", ""))
        if m:
            return _vec([float(x) for x in m.group(1).split(",")])
        for r in f.get("refs") or []:
            parts = r.split(":")
            if len(parts) >= 4 and parts[2][:1] in "+-" and len(parts[2]) == 2:
                v = [0.0, 0.0, 0.0]
                v["XYZ".index(parts[2][1])] = 1.0 if parts[2][0] == "+" else -1.0
                return _vec(v)
        # no entry reference: drill against the primary datum (a plane datum's direction is
        # its outward normal, so the entry side is the opposite face; an axis points out)
        for d in self.plan.get("datums") or []:
            if d["role"] == "primary":
                v = d["direction"] if d["kind"] == "axis" else [-x for x in d["direction"]]
                return _vec(v)
        raise Unsupported("hole without an entry direction")

    def hole(self, f: dict) -> None:
        prm = f["params"]
        z = self.hole_dir(f)
        pos = ", ".join(f"Location(Plane(origin={_vec(p)}, z_dir={z}))"
                        for p in prm["positions"])
        depth = "None" if prm.get("through", "depth" not in prm) else _num(prm["depth"])
        kind = prm.get("kind", "simple")
        self.emit("with BuildPart() as _bp:")
        self.emit("    add(body)")
        self.emit(f"    with Locations({pos}):")
        if kind == "simple":
            self.emit(f"        Hole(radius={_num(prm['diameter'])} / 2, depth={depth})")
        elif kind == "counterbore":
            self.emit(f"        CounterBoreHole(radius={_num(prm['diameter'])} / 2, "
                      f"counter_bore_radius={_num(prm['cb_diameter'])} / 2, "
                      f"counter_bore_depth={_num(prm['cb_depth'])}, depth={depth})")
        else:
            raise Unsupported(f"hole kind {kind}")
        self.emit("body = _bp.part")

    # whole file ------------------------------------------------------------------------------
    def write(self) -> str:
        p = self.plan
        self.emit(f'"""{p["part_id"]} - cad/part.py written by the ReCADBench replay agent from '
                  f'plan/re_plan.v{p["revision"]}.json."""')
        self.emit()
        self.emit("import math")
        self.emit()
        self.emit("from build123d import (Axis, BuildLine, BuildPart, BuildSketch, CenterArc, "
                  "Circle, CounterBoreHole,")
        self.emit("                       GeomType, Hole, Line, Location, Locations, Plane, "
                  "Polygon, Rectangle, Rot,")
        self.emit("                       SlotCenterToCenter, Vector, add, chamfer, extrude, "
                  "fillet, make_face, revolve, Box,")
        self.emit("                       Cylinder)")
        self.emit()
        for prm in p.get("parameters") or []:
            v = prm["value"]
            lit = repr(v) if isinstance(v, int) and not isinstance(v, bool) else repr(float(v))
            self.emit(f"{prm['name']} = {lit}  # IR parameter")
        self.emit(HELPERS)
        self.emit("body = None")
        self.emit("owned = {}")
        self.emit("skipped = []")
        self.emit("sharp = {}")
        self.emit()
        bad = []
        for f in p["features"]:
            mark = len(self.lines)
            try:
                self.feature(f)
            except (Unsupported, KeyError) as exc:
                del self.lines[mark:]
                bad.append(f"{f['id']}: {exc}")
                self.emit(f"# {f['id']} {f['type']}: not built ({exc})")
                self.emit()
        self.emit("part = body")
        self.skipped = bad
        return "\n".join(self.lines) + "\n"


def write_part_py(plan: dict) -> tuple[str, list[str]]:
    """``(source of cad/part.py, features that could not be written)``."""
    w = Writer(plan)
    src = w.write()
    return src, w.skipped
