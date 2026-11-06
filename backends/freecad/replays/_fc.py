"""Shared helpers for the FreeCAD conformance replays (PartDesign only, no dumb shapes).

Every replay is a standalone script run with FreeCAD's Python:

    python C01_block.py <out.FCStd>

It builds one ``PartDesign::Body`` plus a ``Spreadsheet::Sheet`` named ``Params`` whose aliases are
the IR parameter names; every driving dimension that is an IR parameter is bound to it by an
expression (``Params.<name>``). Plain IR numbers stay literal. The document is saved to argv[1].
"""

from __future__ import annotations

import os
import sys


def _bootstrap() -> None:
    try:
        import FreeCAD  # noqa: F401
        return
    except ImportError:
        pass
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for cand in (
        os.environ.get("FREECAD_LIB"),
        os.path.join(sys.prefix, "lib"),
        os.path.join(sys.prefix, "Library", "bin"),
        os.path.join(sys.prefix, "bin"),
        exe_dir,
        os.path.join(exe_dir, "..", "lib"),
    ):
        if cand and os.path.isdir(cand) and cand not in sys.path:
            sys.path.append(cand)
            try:
                import FreeCAD  # noqa: F401
                return
            except ImportError:
                sys.path.remove(cand)
    raise SystemExit("cannot import FreeCAD; set FREECAD_LIB to the directory holding FreeCAD.so")


_bootstrap()

import FreeCAD  # noqa: E402
import Part  # noqa: E402
import PartDesign  # noqa: E402, F401
import Sketcher  # noqa: E402

V = FreeCAD.Vector


def new_body(name: str):
    doc = FreeCAD.newDocument(name)
    body = doc.addObject("PartDesign::Body", "Body")
    return doc, body


def params(doc, values: dict[str, float]):
    """Spreadsheet ``Params``: column A = name, column B = value with alias == IR name."""
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    for row, (name, value) in enumerate(values.items(), start=1):
        sheet.set(f"A{row}", name)
        sheet.set(f"B{row}", repr(float(value)))
        sheet.setAlias(f"B{row}", name)
    doc.recompute()
    return sheet


def origin(body, role: str):
    """Body origin feature by role: X_Axis, Y_Axis, Z_Axis, XY_Plane, XZ_Plane, YZ_Plane."""
    for f in body.Origin.OriginFeatures:
        if f.Role == role:
            return f
    raise KeyError(role)


def sketch(body, plane: str, offset: float = 0.0, name: str = "Sketch", offset_expr: str = ""):
    """Sketch on a body origin plane, shifted ``offset`` along the plane's normal.

    XY_Plane: sketch (x, y) = world (x, y), normal +Z.
    XZ_Plane: sketch (x, y) = world (x, z), normal -Y (FreeCAD >= 1.0), = IR ``P_xz``.
    YZ_Plane: sketch (x, y) = world (y, z), normal +X.
    """
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(origin(body, plane), "")]
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = FreeCAD.Placement(V(0, 0, offset), FreeCAD.Rotation())
    if offset_expr:
        sk.setExpression(".AttachmentOffset.Base.z", offset_expr)
    return sk


def polygon(sk, pts: list[tuple[float, float]]) -> list[int]:
    """Closed polyline with coincident corners (no dimensional constraints)."""
    ids = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        ids.append(sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0))))
    for i in range(n):
        sk.addConstraint(Sketcher.Constraint("Coincident", ids[i], 2, ids[(i + 1) % n], 1))
    return ids


def rect(sk, cx: float, cy: float, w: float, h: float) -> list[int]:
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    ids = polygon(sk, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    sk.addConstraint(Sketcher.Constraint("Horizontal", ids[0]))
    sk.addConstraint(Sketcher.Constraint("Horizontal", ids[2]))
    sk.addConstraint(Sketcher.Constraint("Vertical", ids[1]))
    sk.addConstraint(Sketcher.Constraint("Vertical", ids[3]))
    return ids


def circle(sk, cx: float, cy: float, r: float) -> int:
    return sk.addGeometry(Part.Circle(V(cx, cy, 0), V(0, 0, 1), r))


def bind(obj, prop: str, param: str) -> None:
    obj.setExpression(prop, f"Params.{param}")


def edges_where(shape, pred) -> list[str]:
    """Names (``EdgeN``) of the edges of ``shape`` whose geometry satisfies ``pred(edge)``."""
    return [f"Edge{i}" for i, e in enumerate(shape.Edges, start=1) if pred(e)]


def faces_where(shape, pred) -> list[str]:
    return [f"Face{i}" for i, f in enumerate(shape.Faces, start=1) if pred(f)]


def planar_normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def finish(doc, body, tip=None) -> None:
    if tip is not None:
        body.Tip = tip
    doc.recompute()
    bad = [o.Name for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
    if bad:
        raise SystemExit(f"recompute failed: {bad}")
    if len(sys.argv) < 2:
        raise SystemExit("usage: python <replay>.py <out.FCStd>")
    doc.saveAs(os.path.abspath(sys.argv[1]))
    shape = body.Shape
    print(f"replay ok: solids={len(shape.Solids)} volume={shape.Volume:.4f} "
          f"faces={len(shape.Faces)}", file=sys.stderr)
