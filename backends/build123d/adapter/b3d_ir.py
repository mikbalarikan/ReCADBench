"""Deterministic RE Plan IR (spec 0.3.0) -> build123d script (`recad build123d execute-ir`).

Pure stdlib: this module only *writes* a build123d script; adapter.py runs it. The emitted
script uses the builder API only (BuildPart/BuildSketch + traced operations), names its driving
dimensions after IR `parameters[].name` (module-level assignments, so `set-param` works on it)
and assigns the final solid to `part`. Selectors are resolved at run time by small helpers in
the script that use build123d queries only (no kernel access).

Translation conventions (in addition to backends/conformance/ir_cases/README.md):
* feature selector `<F>:<type>:<dir>:<ext>[:n]` = current faces lying on a surface that feature
  F created (surface signature tracked per feature), filtered by type and outward normal,
  extremum along the selector's axis coordinate, optional index after deterministic sorting.
* edge sets: a face selector = boundary edges of those faces; `<F>:edges:all` = all edges of
  F's faces (other edge filters unsupported).
* hole: positions are projected onto the current entry face; axis = inward normal;
  `through` => build123d through-all hole.
* a pattern whose seeds are holes/primitives re-emits the seeds inside the pattern's location
  context (seed + pattern, the build123d form the extract convention counts as 2).
* mirror: only of all preceding features (the whole body), as build123d's builder mirror.
* sketch numbers given as `{"ref": p}` (spec 0.3.0) are emitted as the parameter name, so
  `set-param` drives them (a rect keeps its center). The older `sketch.constraints` binding
  `<entity_id>.<width|height|radius|diameter> = <parameter>` (the min edge stays fixed) still
  works for plain numbers.
* pattern_circular (ADR 0004): instance k at k * step about the axis, step = angle_deg / count
  (angle_deg = total sweep, default 360) or step = pitch_deg.
* extrude `symmetric: true` => `distance` is the total length (half on each side).
Unsupported (error): sweep, loft, rib, boolean, thread_cosmetic, freeform_region, extrude
`distance2`, patterns of non-placeable seeds, partial mirrors, edge filters other than `all`.
"""

from __future__ import annotations

import math
import re

TAXONOMY_TYPES = (
    "primitive", "extrude", "revolve", "sweep", "loft", "hole", "fillet", "chamfer", "shell",
    "draft", "rib", "boolean", "pattern_linear", "pattern_circular", "mirror",
    "thread_cosmetic", "freeform_region",
)
ALLOWED_PARAMS = {
    "primitive": {"shape", "size", "center", "axis"},
    "extrude": {"distance", "distance2", "direction", "taper_deg", "symmetric"},
    "revolve": {"angle_deg", "axis"},
    "sweep": {"path"},
    "loft": {"sections"},
    "hole": {"diameter", "depth", "through", "kind", "cb_diameter", "cb_depth", "csk_diameter",
             "csk_angle_deg", "positions"},
    "fillet": {"radius", "edges"},
    "chamfer": {"distance", "distance2", "angle_deg", "edges"},
    "shell": {"thickness", "open_faces"},
    "draft": {"angle_deg", "faces", "neutral"},
    "rib": {"thickness"},
    "boolean": {"tool_feature"},
    "pattern_linear": {"features", "direction", "count", "spacing", "direction2", "count2",
                       "spacing2"},
    "pattern_circular": {"features", "axis", "count", "angle_deg", "pitch_deg"},
    "mirror": {"features", "plane"},
    "thread_cosmetic": {"designation", "length"},
    "freeform_region": {"faces", "max_patches"},
}
NEEDS_SKETCH = {"extrude", "revolve", "sweep", "rib"}
SELECTOR_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(:(plane|cylinder|cone|sphere|torus|bspline|any):([+-][XYZ]|axis|any)"
    r":(max|min|any)(:[0-9]+)?|:edges:(all|outer|inner|convex|concave))?$"
)
SELECTOR_KEYS = ("edges", "faces", "open_faces", "neutral", "plane", "axis", "direction", "path")
UNSUPPORTED = ("sweep", "loft", "rib", "boolean", "thread_cosmetic", "freeform_region")
PLACEABLE = ("hole", "primitive")
BIND_RE = re.compile(
    r"^\s*([A-Za-z]\w*)\.(width|height|radius|diameter)\s*=\s*([a-z][a-z0-9_]*)\b"
)


SPEC_VERSION = "0.3.0"


class IRError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------
# validation (structural + the semantic rules of spec/README.md that execution relies on)
# ---------------------------------------------------------------------------------------------


def validate(ir: dict) -> list[str]:
    errs: list[str] = []

    def req(obj: dict, keys: tuple[str, ...], where: str) -> None:
        for k in keys:
            if k not in obj:
                errs.append(f"{where}: missing '{k}'")

    if not isinstance(ir, dict):
        return ["IR is not a JSON object"]
    req(ir, ("spec_version", "part_id", "revision", "units", "datums", "features"), "IR")
    if errs:
        return errs
    if ir["spec_version"] != SPEC_VERSION:
        hint = " (upgrade with `recadbench-spec migrate`)" if ir["spec_version"] == "0.2.0" else ""
        errs.append(f"spec_version {ir['spec_version']!r} != {SPEC_VERSION!r}{hint}")
    if ir["units"] != "mm":
        errs.append("units must be 'mm'")
    ids: list[str] = []
    datums = ir.get("datums") or []
    if not datums:
        errs.append("datums: at least one required")
    if sum(1 for d in datums if d.get("role") == "primary") != 1:
        errs.append("datums: exactly one primary datum required")
    for d in datums:
        req(d, ("id", "role", "kind", "origin", "direction"), f"datum {d.get('id')}")
        if d.get("kind") == "plane" and "x_dir" not in d:
            errs.append(f"datum {d.get('id')}: plane needs x_dir")
        for k in ("direction", "x_dir"):
            if k in d and not any(abs(c) > 0 for c in d[k]):
                errs.append(f"datum {d.get('id')}: zero vector '{k}'")
        ids.append(d.get("id"))
    for p in ir.get("reference_planes") or []:
        req(p, ("id", "origin", "normal", "x_dir"), f"reference plane {p.get('id')}")
        ids.append(p.get("id"))
    params = {p.get("name") for p in ir.get("parameters") or []}
    if len(params) != len(ir.get("parameters") or []):
        errs.append("parameters: duplicate names")
    known = set(ids)
    feats = ir["features"]
    if not feats:
        errs.append("features: at least one required")
    for f in feats:
        fid = f.get("id", "?")
        req(f, ("id", "type", "params"), f"feature {fid}")
        t = f.get("type")
        if t not in TAXONOMY_TYPES:
            errs.append(f"feature {fid}: unknown type {t!r}")
            continue
        extra = set(f.get("params", {})) - ALLOWED_PARAMS[t]
        if extra:
            errs.append(f"feature {fid}: params not allowed for {t}: {sorted(extra)}")
        if t in NEEDS_SKETCH and "sketch" not in f:
            errs.append(f"feature {fid}: {t} needs a sketch")
        if f.get("operation", "add") not in ("new", "add", "cut", "intersect"):
            errs.append(f"feature {fid}: bad operation {f.get('operation')!r}")

        def check_sel(s: str, where: str, fid: str = fid) -> None:
            if not SELECTOR_RE.match(s):
                errs.append(f"feature {fid}: {where}: bad selector {s!r}")
            elif s.split(":")[0] not in known:
                errs.append(f"feature {fid}: {where}: unknown or later id in {s!r}")

        for k, v in f.get("params", {}).items():
            if isinstance(v, dict):
                if v.get("ref") not in params:
                    errs.append(f"feature {fid}: {k}: undeclared parameter {v.get('ref')!r}")
            elif isinstance(v, str) and k in SELECTOR_KEYS:
                check_sel(v, k)
            elif isinstance(v, list) and k in SELECTOR_KEYS:
                for s in v:
                    if isinstance(s, str):
                        check_sel(s, k)
        if "sketch" in f:
            check_sel(f["sketch"].get("plane", ""), "sketch.plane")
            for name in _sketch_refs(f["sketch"]):
                if name not in params:
                    errs.append(f"feature {fid}: sketch: undeclared parameter {name!r}")
        if t == "pattern_circular" and {"angle_deg", "pitch_deg"} <= set(f.get("params", {})):
            errs.append(f"feature {fid}: give at most one of angle_deg / pitch_deg")
        for s in f.get("refs") or []:
            check_sel(s, "refs")
        if t in ("pattern_linear", "pattern_circular", "mirror"):
            fl = f.get("params", {}).get("features")
            if not isinstance(fl, list) or not fl:
                errs.append(f"feature {fid}: 'features' must be a non-empty list")
            else:
                for x in fl:
                    if x not in known or x in ids:
                        errs.append(f"feature {fid}: features: {x!r} is not an earlier feature")
        if fid in known:
            errs.append(f"duplicate id {fid!r}")
        known.add(fid)
    return errs


def _sketch_refs(sketch: dict) -> list[str]:
    out: list[str] = []

    def walk(v) -> None:
        if isinstance(v, dict) and "ref" in v:
            out.append(v["ref"])
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for e in sketch.get("entities") or []:
        for v in e.values():
            walk(v)
    return out


# ---------------------------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------------------------

HELPERS = '''
# --- selector helpers (build123d queries only; generated, do not edit) ---------------------
_FEATURE_SURFACES = {}


def _sig(face):
    """Signature of the surface a face lies on (stable under later cuts of the face)."""
    kind = face.geom_type.name
    if kind == "PLANE":
        n = face.normal_at()
        return ("PLANE", round(n.X, 4), round(n.Y, 4), round(n.Z, 4),
                round(n.dot(face.center()), 4))
    axis = face.axis_of_rotation if kind in ("CYLINDER", "CONE", "TORUS") else None
    if axis is not None:
        d = axis.direction
        if (d.X, d.Y, d.Z) < (0, 0, 0):
            d = -d
        p = axis.position - d * axis.position.dot(d)
        r = face.radius if kind == "CYLINDER" else 0
        return (kind, round(d.X, 4), round(d.Y, 4), round(d.Z, 4), round(p.X, 4),
                round(p.Y, 4), round(p.Z, 4), round(r or 0, 4))
    c = face.center()
    return (kind, round(c.X, 3), round(c.Y, 3), round(c.Z, 3))


def _faces(bp):
    return list(bp.faces()) if bp.part is not None else []


def _surfaces(bp):
    return {_sig(f) for f in _faces(bp)}


def _track(bp, fid, before):
    _FEATURE_SURFACES[fid] = _surfaces(bp) - before


_AXES = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}


def _sel(bp, selector):
    parts = selector.split(":")
    fid, kind, d, ext = parts[:4]
    sigs = _FEATURE_SURFACES.get(fid, set())
    faces = [f for f in _faces(bp) if _sig(f) in sigs]
    if kind != "any":
        faces = [f for f in faces if f.geom_type.name == kind.upper()]
    if d not in ("any", "axis"):
        v = _AXES[d[1]] * (1 if d[0] == "+" else -1)
        keep = []
        for f in faces:
            if f.geom_type.name == "PLANE":
                ok = f.normal_at().dot(v) > 1 - 1e-6
            else:
                ax = f.axis_of_rotation
                ok = ax is not None and abs(ax.direction.dot(v)) > 1 - 1e-6
            if ok:
                keep.append(f)
        faces = keep
    faces.sort(key=lambda f: (round(f.center().X, 6), round(f.center().Y, 6),
                              round(f.center().Z, 6)))
    if ext != "any" and faces:
        if d in ("any", "axis"):
            raise ValueError(f"selector {selector}: extremum needs a +/-X/Y/Z direction")
        vals = [getattr(f.center(), d[1]) for f in faces]
        m = max(vals) if ext == "max" else min(vals)
        faces = [f for f, val in zip(faces, vals) if abs(val - m) < 1e-6]
    if len(parts) == 5:
        faces = [faces[int(parts[4])]]
    if not faces:
        raise ValueError(f"selector {selector} matched no face")
    return faces


def _sel_faces(bp, selectors):
    out = []
    for s in [selectors] if isinstance(selectors, str) else selectors:
        out.extend(_sel(bp, s))
    return out


def _sel_edges(bp, spec):
    edges = {}
    for s in [spec] if isinstance(spec, str) else spec:
        parts = s.split(":")
        if len(parts) == 3 and parts[1] == "edges":
            if parts[2] != "all":
                raise ValueError(f"edge filter {parts[2]!r} is not supported")
            sigs = _FEATURE_SURFACES.get(parts[0], set())
            faces = [f for f in _faces(bp) if _sig(f) in sigs]
        else:
            faces = _sel(bp, s)
        for f in faces:
            for e in f.edges():
                edges[hash(e)] = e
    return [edges[k] for k in sorted(edges)]


def _face_plane(face):
    return Plane(origin=face.center(), z_dir=face.normal_at())


def _hole_loc(bp, entry, pos):
    face = _sel(bp, entry)[0]
    n = face.normal_at()
    p = Vector(*pos)
    p = p - n * (p - face.center()).dot(n)
    return Location(Plane(origin=p, z_dir=n))


def _reflect(sigs, plane):
    out = set()
    m, o = plane.z_dir, plane.origin
    for s in sigs:
        if s[0] != "PLANE":
            continue
        n = Vector(s[1], s[2], s[3])
        p = n * s[4]
        n2 = n - m * (2 * n.dot(m))
        p2 = p - m * (2 * (p - o).dot(m))
        out.add(("PLANE", round(n2.X, 4), round(n2.Y, 4), round(n2.Z, 4),
                 round(n2.dot(p2), 4)))
    return out
'''


def _num(x: float | int) -> str:
    if isinstance(x, bool):
        return repr(x)
    if isinstance(x, int):
        return repr(x)
    if float(x).is_integer():
        return repr(float(x))
    return repr(float(x))


def _vec(v: list) -> str:
    return "(" + ", ".join(_num(c) for c in v) + ")"


class _Gen:
    def __init__(self, ir: dict):
        self.ir = ir
        self.params = {p["name"]: p["value"] for p in ir.get("parameters") or []}
        self.datums = {d["id"]: d for d in ir["datums"]}
        self.planes = {p["id"]: p for p in ir.get("reference_planes") or []}
        self.feats = {f["id"]: f for f in ir["features"]}
        self.lines: list[str] = []
        self.warnings: list[str] = []
        self.deferred: dict[str, str] = {}  # seed id -> pattern id
        self.done: list[str] = []

    # -- expressions ---------------------------------------------------------------------------
    def ex(self, v) -> str:
        if isinstance(v, dict):
            return v["ref"]
        if isinstance(v, (int, float)):
            return _num(v)
        raise IRError(f"expected a number or parameter ref, got {v!r}")

    def val(self, v) -> float:
        return float(self.params[v["ref"]]) if isinstance(v, dict) else float(v)

    def emit(self, line: str, ind: int = 1) -> None:
        self.lines.append("    " * ind + line)

    def plane_expr(self, sel: str) -> str:
        if sel in self.datums:
            d = self.datums[sel]
            if d["kind"] != "plane":
                raise IRError(f"{sel} is not a plane datum")
            return (f"Plane(origin={_vec(d['origin'])}, x_dir={_vec(d['x_dir'])}, "
                    f"z_dir={_vec(d['direction'])})")
        if sel in self.planes:
            p = self.planes[sel]
            return (f"Plane(origin={_vec(p['origin'])}, x_dir={_vec(p['x_dir'])}, "
                    f"z_dir={_vec(p['normal'])})")
        return f"_face_plane(_sel(bp, {sel!r})[0])"

    def plane_vectors(self, sel: str) -> tuple[list, list]:
        if sel in self.datums:
            return self.datums[sel]["origin"], self.datums[sel]["direction"]
        if sel in self.planes:
            return self.planes[sel]["origin"], self.planes[sel]["normal"]
        raise IRError(f"{sel}: a datum or reference plane id is required here")

    def axis_expr(self, sel) -> str:
        if isinstance(sel, str) and (sel in self.datums or sel in self.planes):
            o, d = self.plane_vectors(sel)
            return f"Axis({_vec(o)}, {_vec(d)})"
        raise IRError(f"axis {sel!r}: a datum axis/plane id is required")

    @staticmethod
    def mode(op: str | None) -> str:
        return {"cut": "Mode.SUBTRACT", "intersect": "Mode.INTERSECT"}.get(op or "add",
                                                                        "Mode.ADD")

    # -- sketch --------------------------------------------------------------------------------
    def _numeric(self, e: dict) -> dict:
        """Entity with every {"ref"} replaced by its parameter value (for geometry checks)."""

        def conv(v):
            if isinstance(v, dict) and "ref" in v:
                return float(self.params[v["ref"]])
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v

        return {k: conv(v) for k, v in e.items()}

    def v2(self, v: list) -> str:
        return "(" + ", ".join(self.ex(c) for c in v) + ")"

    @staticmethod
    def _poly(e: dict) -> list[tuple[float, float]]:
        k = e["kind"]
        if k == "rect":
            cx, cy = e["center"]
            w, h = e["width"] / 2, e["height"] / 2
            a = math.radians(e.get("angle_deg", 0))
            pts = [(-w, -h), (w, -h), (w, h), (-w, h)]
            return [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a)
                     + y * math.cos(a)) for x, y in pts]
        if k == "circle":
            cx, cy = e["center"]
            r = e["radius"]
            return [(cx + r * math.cos(2 * math.pi * i / 64), cy + r * math.sin(2 * math.pi * i
                                                                               / 64))
                    for i in range(64)]
        if k == "slot":
            (x1, y1), (x2, y2), r = e["c1"], e["c2"], e["radius"]
            a = math.atan2(y2 - y1, x2 - x1)
            pts = []
            for i in range(17):
                t = a - math.pi / 2 + math.pi * i / 16
                pts.append((x2 + r * math.cos(t), y2 + r * math.sin(t)))
            for i in range(17):
                t = a + math.pi / 2 + math.pi * i / 16
                pts.append((x1 + r * math.cos(t), y1 + r * math.sin(t)))
            return pts
        if k == "polygon":
            return [tuple(p) for p in e["points"]]
        return []

    @staticmethod
    def _inside(pt: tuple[float, float], poly: list) -> bool:
        x, y = pt
        inside = False
        n = len(poly)
        for i in range(n):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if xi > x:
                    inside = not inside
        return inside

    def sketch(self, f: dict) -> None:
        sk = f["sketch"]
        binds: dict[tuple[str, str], str] = {}
        for c in sk.get("constraints") or []:
            m = BIND_RE.match(c)
            if m and m.group(3) in self.params:
                binds[(m.group(1), m.group(2))] = m.group(3)
        closed = [e for e in sk["entities"]
                  if e["kind"] in ("rect", "circle", "slot", "polygon")]
        loose = [e for e in sk["entities"] if e["kind"] in ("line", "arc", "spline")
                 and not e.get("construction")]
        closed = [e for e in closed if not e.get("construction")]
        polys = [self._poly(self._numeric(e)) for e in closed]
        depth = []
        for i, pi in enumerate(polys):
            d = 0
            for j, pj in enumerate(polys):
                if i != j and all(self._inside(p, pj) for p in pi[:: max(1, len(pi) // 16)]):
                    d += 1
            depth.append(d)
        order = sorted(range(len(closed)), key=lambda i: (depth[i], i))
        self.emit(f"with BuildSketch({self.plane_expr(sk['plane'])}):")
        for i in order:
            self.entity(closed[i], "Mode.SUBTRACT" if depth[i] % 2 else "Mode.ADD", binds)
        if loose:
            self.emit("with BuildLine():", 2)
            for e in loose:
                k = e["kind"]
                if k == "line":
                    self.emit(f"Line({self.v2(e['p1'])}, {self.v2(e['p2'])})", 3)
                elif k == "arc":
                    span = f"({self.ex(e['end_deg'])}) - ({self.ex(e['start_deg'])})"
                    if not any(isinstance(e[x], dict) for x in ("start_deg", "end_deg")):
                        span = _num(e["end_deg"] - e["start_deg"])
                    self.emit(f"CenterArc({self.v2(e['center'])}, {self.ex(e['radius'])}, "
                              f"{self.ex(e['start_deg'])}, {span})", 3)
                else:
                    pts = ", ".join(self.v2(p) for p in e["points"])
                    if e.get("closed"):
                        pts += ", " + self.v2(e["points"][0])
                    self.emit(f"Spline({pts})", 3)
            self.emit("make_face()", 2)

    def entity(self, e: dict, mode: str, binds: dict) -> None:
        k, eid = e["kind"], e.get("id")
        n = self._numeric(e)
        if k == "rect":
            cx, cy = n["center"]
            w, h = self.ex(e["width"]), self.ex(e["height"])
            cxs, cys = self.ex(e["center"][0]), self.ex(e["center"][1])
            if (eid, "width") in binds and not isinstance(e["width"], dict):
                w = binds[(eid, "width")]
                cxs = f"{_num(cx - n['width'] / 2)} + {w} / 2"
            if (eid, "height") in binds and not isinstance(e["height"], dict):
                h = binds[(eid, "height")]
                cys = f"{_num(cy - n['height'] / 2)} + {h} / 2"
            rot = f", rotation={self.ex(e['angle_deg'])}" if e.get("angle_deg") else ""
            self.emit(f"with Locations(({cxs}, {cys})):", 2)
            self.emit(f"Rectangle({w}, {h}{rot}, mode={mode})", 3)
        elif k == "circle":
            r = self.ex(e["radius"])
            if isinstance(e["radius"], dict):
                pass
            elif (eid, "radius") in binds:
                r = binds[(eid, "radius")]
            elif (eid, "diameter") in binds:
                r = f"{binds[(eid, 'diameter')]} / 2"
            self.emit(f"with Locations({self.v2(e['center'])}):", 2)
            self.emit(f"Circle({r}, mode={mode})", 3)
        elif k == "slot":
            # slot geometry (centre, angle, length) is derived from the centres: emitted as
            # numbers; only the radius stays a driving parameter when it is a {"ref"}
            (x1, y1), (x2, y2) = n["c1"], n["c2"]
            ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
            dist = math.hypot(x2 - x1, y2 - y1)
            width = f"2 * {self.ex(e['radius'])}" if isinstance(e["radius"], dict) else _num(
                2 * e["radius"])
            self.emit(f"with Locations(Location(({_num((x1 + x2) / 2)}, {_num((y1 + y2) / 2)})"
                      f", (0, 0, 1), {_num(ang)})):", 2)
            self.emit(f"SlotCenterToCenter({_num(dist)}, {width}, mode={mode})", 3)
        elif k == "polygon":
            pts = ", ".join(self.v2(p) for p in e["points"])
            self.emit(f"Polygon({pts}, align=None, mode={mode})", 2)

    # -- features ------------------------------------------------------------------------------
    def run(self) -> str:
        ir = self.ir
        for f in ir["features"]:
            if f["type"] in UNSUPPORTED:
                raise IRError(f"feature {f['id']}: type {f['type']} is not supported by "
                              "execute-ir")
        for f in ir["features"]:
            if f["type"] in ("pattern_linear", "pattern_circular"):
                for s in f["params"]["features"]:
                    st = self.feats[s]["type"]
                    if st not in PLACEABLE:
                        raise IRError(f"feature {f['id']}: pattern of a {st} seed is not "
                                      "supported (seeds must be hole/primitive)")
                    if s in self.deferred:
                        raise IRError(f"feature {s} is the seed of two patterns")
                    self.deferred[s] = f["id"]
        head = [
            '"""build123d native model generated by `recad build123d execute-ir`.',
            "",
            f"IR: part_id={ir['part_id']} revision={ir['revision']} (spec {ir['spec_version']})."
            ,
            "Deterministic translation; driving dimensions are the IR parameters below.",
            '"""',
            "",
            "from build123d import (",
            "    Axis, BuildLine, BuildPart, BuildSketch, Box, CenterArc, Circle, Cone, "
            "CounterBoreHole,",
            "    CounterSinkHole, Cylinder, Hole, Line, Location, Locations, Mode, Plane, "
            "PolarLocations,",
            "    Polygon, Rectangle, SlotCenterToCenter, Sphere, Spline, Torus, Vector, chamfer, "
            "draft,",
            "    extrude, fillet, make_face, mirror, offset, revolve,",
            ")",
            "",
            "# IR parameters (driving dimensions)",
        ]
        for name, value in self.params.items():
            head.append(f"{name} = {_num(value)}")
        head.append(HELPERS)
        self.lines = head + ["", "with BuildPart() as bp:"]
        for f in ir["features"]:
            getattr(self, "f_" + f["type"])(f)
            self.done.append(f["id"])
        self.lines += ["", "part = bp.part", ""]
        return "\n".join(self.lines)

    def _begin(self, f: dict, label: str) -> None:
        self.emit(f"# {f['id']} {f['type']}{label}")
        self.emit("_before = _surfaces(bp)")

    def _end(self, fid: str) -> None:
        self.emit(f"_track(bp, {fid!r}, _before)")

    def f_extrude(self, f: dict) -> None:
        p = f["params"]
        if "distance2" in p:
            raise IRError(f"feature {f['id']}: extrude distance2 is not supported")
        self._begin(f, f" ({f.get('operation', 'add')})")
        self.sketch(f)
        args = [f"amount={self.ex(p['distance'])}"]
        if p.get("symmetric"):
            args = [f"amount={self.ex(p['distance'])} / 2", "both=True"]
        if "direction" in p and not p.get("symmetric"):
            args.append(f"dir={_vec(p['direction'])}")
        if "taper_deg" in p:
            args.append(f"taper={self.ex(p['taper_deg'])}")
        args.append(f"mode={self.mode(f.get('operation'))}")
        self.emit(f"extrude({', '.join(args)})")
        self._end(f["id"])

    def f_revolve(self, f: dict) -> None:
        p = f["params"]
        self._begin(f, f" ({f.get('operation', 'add')})")
        self.sketch(f)
        ang = self.ex(p.get("angle_deg", 360))
        self.emit(f"revolve(axis={self.axis_expr(p.get('axis'))}, revolution_arc={ang}, "
                  f"mode={self.mode(f.get('operation'))})")
        self._end(f["id"])

    def _hole_call(self, f: dict) -> str:
        p = f["params"]
        kind = p.get("kind", "simple")
        r = f"{self.ex(p['diameter'])} / 2"
        depth = "None" if p.get("through") or "depth" not in p else self.ex(p["depth"])
        if kind == "counterbore":
            return (f"CounterBoreHole(radius={r}, counter_bore_radius="
                    f"{self.ex(p['cb_diameter'])} / 2, counter_bore_depth="
                    f"{self.ex(p['cb_depth'])}, depth={depth})")
        if kind == "countersink":
            return (f"CounterSinkHole(radius={r}, counter_sink_radius="
                    f"{self.ex(p['csk_diameter'])} / 2, depth={depth}, counter_sink_angle="
                    f"{self.ex(p.get('csk_angle_deg', 90))})")
        if kind != "simple":
            raise IRError(f"feature {f['id']}: hole kind {kind!r} is not supported")
        return f"Hole(radius={r}, depth={depth})"

    def _placements(self, f: dict) -> str:
        """Python expression: list of Locations for a placeable feature's instances."""
        p = f["params"]
        if f["type"] == "hole":
            refs = f.get("refs") or []
            if not refs:
                raise IRError(f"feature {f['id']}: hole needs its entry face in refs")
            pos = p.get("positions")
            if not pos:
                raise IRError(f"feature {f['id']}: hole needs positions")
            if len(pos) > 1:
                self.warnings.append(
                    f"{f['id']}: hole with {len(pos)} positions is emitted under one "
                    "Locations context; extract counts it as hole + pattern_linear")
            pl = ", ".join(_vec(x) for x in pos)
            return f"[_hole_loc(bp, {refs[0]!r}, x) for x in [{pl}]]"
        c = p.get("center", [0, 0, 0])
        ax = p.get("axis", [0, 0, 1])
        return f"[Location(Plane(origin={_vec(c)}, z_dir={_vec(ax)}))]"

    def _primitive_call(self, f: dict) -> str:
        p = f["params"]
        shape = p.get("shape")
        size = p.get("size")
        sz = size if isinstance(size, list) else [size]
        e = [self.ex(x) for x in sz]
        m = self.mode(f.get("operation"))
        if shape == "box" and len(e) == 3:
            return f"Box({e[0]}, {e[1]}, {e[2]}, mode={m})"
        if shape == "cylinder" and len(e) == 2:
            return f"Cylinder(radius={e[0]} / 2, height={e[1]}, mode={m})"
        if shape == "sphere" and len(e) == 1:
            return f"Sphere(radius={e[0]} / 2, mode={m})"
        if shape == "cone" and len(e) == 3:
            return (f"Cone(bottom_radius={e[0]} / 2, top_radius={e[1]} / 2, height={e[2]}, "
                    f"mode={m})")
        if shape == "torus" and len(e) == 2:
            return f"Torus(major_radius={e[0]} / 2, minor_radius={e[1]} / 2, mode={m})"
        raise IRError(f"feature {f['id']}: primitive shape {shape!r} / size {size!r} not "
                      "supported (box [x,y,z], cylinder [d,h], sphere [d], cone [d1,d2,h], "
                      "torus [D,d])")

    def _placed_call(self, f: dict) -> str:
        return self._hole_call(f) if f["type"] == "hole" else self._primitive_call(f)

    def _placeable(self, f: dict) -> None:
        if f["id"] in self.deferred:
            self.emit(f"# {f['id']} {f['type']}: emitted as the seed of "
                      f"{self.deferred[f['id']]}")
            return
        self._begin(f, "")
        self.emit(f"with Locations(*{self._placements(f)}):")
        self.emit(self._placed_call(f), 2)
        self._end(f["id"])

    f_hole = _placeable
    f_primitive = _placeable

    def _emit_seeds(self, seeds: list[str], ind: int, xform: str = "") -> None:
        for s in seeds:
            sf = self.feats[s]
            self.emit(f"for _seed in {self._placements(sf)}:", ind)
            loc = f"{xform}_seed" if xform else "_seed"
            self.emit(f"with Locations({loc}):", ind + 1)
            self.emit(self._placed_call(sf), ind + 2)

    def _pattern_end(self, f: dict) -> None:
        self._end(f["id"])
        for s in f["params"]["features"]:
            self.emit(f"_FEATURE_SURFACES[{s!r}] = _FEATURE_SURFACES[{f['id']!r}]")

    def f_pattern_linear(self, f: dict) -> None:
        p = f["params"]
        self._begin(f, " (seed + pattern in one location context)")
        d1 = p.get("direction", [1, 0, 0])
        d2 = p.get("direction2", [0, 0, 0])
        n1 = self.ex(p.get("count", 1))
        n2 = self.ex(p.get("count2", 1))
        s1 = self.ex(p.get("spacing", 0))
        s2 = self.ex(p.get("spacing2", 0))
        self.emit(f"_grid = [Location(Vector{_vec(d1)} * (_i * {s1}) + Vector{_vec(d2)} * "
                  f"(_j * {s2})) for _i in range(int({n1})) for _j in range(int({n2}))]")
        self.emit("with Locations(*_grid):")
        self._emit_seeds(p["features"], 2)
        self._pattern_end(f)

    def f_pattern_circular(self, f: dict) -> None:
        p = f["params"]
        self._begin(f, " (seed + pattern in one location context)")
        ax = p.get("axis")
        if not (isinstance(ax, str) and (ax in self.datums or ax in self.planes)):
            raise IRError(f"feature {f['id']}: pattern axis must be a datum/plane id")
        o, d = self.plane_vectors(ax)
        # ADR 0004: step = angle_deg / count (total sweep) or pitch_deg; PolarLocations with
        # endpoint=False spaces `count` instances by angular_range / count
        count = self.ex(p["count"])
        if "pitch_deg" in p:
            sweep = f"{self.ex(p['pitch_deg'])} * {count}"
        else:
            sweep = self.ex(p.get("angle_deg", 360))
        self.emit(f"_axis = Location(Plane(origin={_vec(o)}, z_dir={_vec(d)}))")
        self.emit("with Locations(_axis):")
        self.emit(f"with PolarLocations(0, int({count}), start_angle=0, "
                  f"angular_range={sweep}, endpoint=False):", 2)
        self._emit_seeds(p["features"], 3, xform="_axis.inverse() * ")
        self._pattern_end(f)

    def f_mirror(self, f: dict) -> None:
        p = f["params"]
        if sorted(p["features"]) != sorted(self.done):
            raise IRError(f"feature {f['id']}: mirror supports only all preceding features "
                          f"(got {p['features']}, preceding {self.done})")
        self._begin(f, " (whole body)")
        pl = p.get("plane")
        self.emit(f"_mplane = {self.plane_expr(pl)}")
        self.emit("_old = {k: set(v) for k, v in _FEATURE_SURFACES.items()}")
        self.emit("mirror(about=_mplane)")
        self._end(f["id"])
        self.emit("for _k, _v in _old.items():")
        self.emit("_FEATURE_SURFACES[_k] = _v | _reflect(_v, _mplane)", 2)

    def f_fillet(self, f: dict) -> None:
        p = f["params"]
        self._begin(f, "")
        self.emit(f"fillet(_sel_edges(bp, {p['edges']!r}), radius={self.ex(p['radius'])})")
        self._end(f["id"])

    def f_chamfer(self, f: dict) -> None:
        p = f["params"]
        self._begin(f, "")
        extra = ""
        if "distance2" in p:
            extra = f", length2={self.ex(p['distance2'])}"
        elif "angle_deg" in p:
            extra = f", angle={self.ex(p['angle_deg'])}"
        self.emit(f"chamfer(_sel_edges(bp, {p['edges']!r}), length={self.ex(p['distance'])}"
                  f"{extra})")
        self._end(f["id"])

    def f_shell(self, f: dict) -> None:
        p = f["params"]
        self._begin(f, " (positive thickness = inward)")
        op = p.get("open_faces")
        opening = f", openings=_sel_faces(bp, {op!r})" if op else ""
        self.emit(f"offset(amount=-({self.ex(p['thickness'])}){opening})")
        self._end(f["id"])

    def f_draft(self, f: dict) -> None:
        p = f["params"]
        self._begin(f, " (pull = outward normal of the neutral face)")
        self.emit(f"draft(_sel_faces(bp, {p['faces']!r}), neutral_plane=_face_plane("
                  f"_sel(bp, {p['neutral']!r})[0]), angle={self.ex(p['angle_deg'])})")
        self._end(f["id"])


def generate(ir: dict) -> tuple[str, list[str]]:
    """IR dict -> (build123d script source, warnings). Raises IRError."""
    errs = validate(ir)
    if errs:
        raise IRError("invalid IR: " + "; ".join(errs))
    g = _Gen(ir)
    return g.run(), g.warnings
