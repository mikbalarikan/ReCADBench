#!/usr/bin/env python
"""recad freecad adapter: measurement instrument for the FreeCAD backend (L1, 00_DECISIONS D3).

Run with FreeCAD's own Python (stdlib + FreeCAD only; never imports recadbench_spec):

    python adapter.py export    <native.FCStd> --out <file.step>
    python adapter.py extract   <native.FCStd> --out <features.json>
    python adapter.py set-param <native.FCStd> <name> <value> --out <file.step> [--native-out <f>]
    python adapter.py replay    <case_id> --out-dir <dir>

Each subcommand prints exactly one JSON object on stdout (all FreeCAD/OCCT chatter goes to
stderr) and exits 0 on success, 1 on failure.

Native model: an .FCStd holding one PartDesign::Body; driving dimensions live in a
Spreadsheet::Sheet (normally named ``Params``) whose cell aliases are the IR parameter names and
are bound to feature properties by expressions. ``extract`` derives the feature list from the
document tree (TypeId of each Body feature), never from agent self-report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPLAYS = HERE.parent / "replays"
TOOL = "freecad"

# ---------------------------------------------------------------------------------------------
# stdout discipline: OCCT writes transfer statistics straight to fd 1. Keep the real stdout for
# the single JSON result and send everything else (C++ and Python) to stderr.
# ---------------------------------------------------------------------------------------------
_RESULT_FD: int | None = None


def _redirect_stdout() -> None:
    global _RESULT_FD
    if _RESULT_FD is None:
        sys.stdout.flush()
        _RESULT_FD = os.dup(1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr


def emit(obj: dict, ok: bool | None = None) -> int:
    ok = obj.get("ok", True) if ok is None else ok
    line = (json.dumps(obj, sort_keys=False) + "\n").encode()
    os.write(_RESULT_FD if _RESULT_FD is not None else 1, line)
    return 0 if ok else 1


def log(msg: str) -> None:
    print(f"[recad freecad] {msg}", file=sys.stderr)


def _bootstrap_freecad() -> None:
    try:
        import FreeCAD  # noqa: F401
        return
    except ImportError:
        pass
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for cand in (
        os.environ.get("FREECAD_LIB"),
        os.path.join(sys.prefix, "lib"),  # conda-forge freecad (Linux/macOS)
        os.path.join(sys.prefix, "Library", "bin"),  # conda-forge freecad (Windows)
        os.path.join(sys.prefix, "bin"),
        exe_dir,  # FreeCAD bundle: bin/python.exe next to FreeCAD.pyd
        os.path.join(exe_dir, "..", "lib"),
    ):
        if cand and os.path.isdir(cand) and cand not in sys.path:
            sys.path.append(cand)
            try:
                import FreeCAD  # noqa: F401
                return
            except ImportError:
                sys.path.remove(cand)
    raise RuntimeError("cannot import FreeCAD; set FREECAD_LIB to the directory with FreeCAD.so")


# ---------------------------------------------------------------------------------------------
# Native type -> taxonomy (spec/recadbench_spec/feature_taxonomy.yaml)
# ---------------------------------------------------------------------------------------------
COUNTED = {
    "primitive", "extrude", "revolve", "sweep", "loft", "hole", "fillet", "chamfer", "shell",
    "draft", "rib", "boolean", "pattern_linear", "pattern_circular", "mirror", "thread_cosmetic",
    "freeform_region",
}

PD_MAP = {
    "PartDesign::Pad": "extrude",
    "PartDesign::Pocket": "extrude",
    "PartDesign::Revolution": "revolve",
    "PartDesign::Groove": "revolve",
    "PartDesign::Hole": "hole",
    "PartDesign::Fillet": "fillet",
    "PartDesign::Chamfer": "chamfer",
    "PartDesign::Thickness": "shell",
    "PartDesign::Draft": "draft",
    "PartDesign::LinearPattern": "pattern_linear",
    "PartDesign::PolarPattern": "pattern_circular",
    "PartDesign::Mirrored": "mirror",
    "PartDesign::Boolean": "boolean",
    "PartDesign::AdditivePipe": "sweep",
    "PartDesign::SubtractivePipe": "sweep",
    "PartDesign::AdditiveHelix": "sweep",
    "PartDesign::SubtractiveHelix": "sweep",
    "PartDesign::AdditiveLoft": "loft",
    "PartDesign::SubtractiveLoft": "loft",
    "Sketcher::SketchObject": "sketch",
    "PartDesign::Plane": "datum_plane",
    "PartDesign::Line": "datum_axis",
    "PartDesign::Point": "datum_point",
    "PartDesign::CoordinateSystem": "datum_plane",
}
for _p in ("Box", "Cylinder", "Sphere", "Cone", "Ellipsoid", "Torus", "Prism", "Wedge"):
    PD_MAP[f"PartDesign::Additive{_p}"] = "primitive"
    PD_MAP[f"PartDesign::Subtractive{_p}"] = "primitive"

# Organisational objects with no geometry of their own: ignored.
IGNORED = {
    "PartDesign::Body", "App::Origin", "App::Line", "App::Plane", "App::Point",
    "App::DatumLine", "App::DatumPlane", "App::DatumPoint", "App::LocalCoordinateSystem",
    "App::Part", "App::DocumentObjectGroup", "App::TextDocument", "App::Annotation",
    "App::MaterialObjectPython",
}
PARAMETER_TYPES = {"Spreadsheet::Sheet", "App::VarSet"}
MULTI = "PartDesign::MultiTransform"
# Not native parametric features (they wrap arbitrary/dumb geometry) => unmapped => gate fail.
UNMAPPED_PD = {
    "PartDesign::FeatureBase": "body BaseFeature (dumb solid as the body's base)",
    "PartDesign::ShapeBinder": "shape binder (external geometry copied into the body)",
    "PartDesign::SubShapeBinder": "sub-shape binder (external geometry copied into the body)",
    "PartDesign::Scaled": "scaled transform (no taxonomy type)",
    "PartDesign::Solid": "generic solid feature",
    "PartDesign::Feature": "generic feature",
}

PARAM_PROPS = (
    "Type", "Length", "Length2", "Reversed", "Midplane", "Angle", "Diameter", "Depth",
    "DepthType", "HoleCutType", "HoleCutDiameter", "HoleCutDepth", "HoleCutCountersinkAngle",
    "Threaded", "Radius", "Size", "Size2", "ChamferType", "Value", "Mode", "Join",
    "Occurrences", "Offset", "Occurrences2", "Offset2", "Mode2", "TransformMode",
)


def _jsonable(v):
    if hasattr(v, "Value") and hasattr(v, "Unit"):  # Base.Quantity
        return float(v.Value)
    if isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return str(v)


def _feature_params(obj) -> dict:
    out = {}
    props = set(obj.PropertiesList)
    for p in PARAM_PROPS:
        if p in props:
            try:
                out[p] = _jsonable(getattr(obj, p))
            except Exception:  # noqa: BLE001 - informational only
                pass
    for attr in ("Originals", "Base", "Profile"):
        if attr in props:
            v = getattr(obj, attr)
            if isinstance(v, tuple) and len(v) == 2 and hasattr(v[0], "Name") \
                    and isinstance(v[1], (list, tuple)):  # link-sub: (obj, [subnames])
                out[attr] = v[0].Name
                if v[1] and any(v[1]):
                    out[attr + "_elements"] = list(v[1])
            elif isinstance(v, (list, tuple)) and v and all(hasattr(o, "Name") for o in v):
                out[attr] = [o.Name for o in v]
            elif hasattr(v, "Name"):
                out[attr] = v.Name
    expr = {k: e for k, e in (obj.ExpressionEngine or [])}
    if expr:
        out["expressions"] = expr
    return out


# ---------------------------------------------------------------------------------------------
# Document helpers
# ---------------------------------------------------------------------------------------------
def _open(native: str):
    import FreeCAD

    path = os.path.abspath(native)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    doc = FreeCAD.openDocument(path)
    doc.recompute()
    return doc


def _errors(doc) -> list[str]:
    return [f"{o.Name}({o.TypeId}): {','.join(o.State)}" for o in doc.Objects
            if "Invalid" in o.State or "Error" in o.State]


def _bodies(doc) -> list:
    return [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]


def _tool_bodies(doc) -> set[str]:
    """Names of bodies consumed as tools by an active PartDesign::Boolean (not result bodies)."""
    out: set[str] = set()
    for o in doc.Objects:
        if o.TypeId == "PartDesign::Boolean" and not getattr(o, "Suppressed", False):
            out.update(t.Name for t in (o.Group or []) if t.TypeId == "PartDesign::Body")
    return out


def _the_body(doc):
    tools = _tool_bodies(doc)
    bodies = [b for b in _bodies(doc)
              if b.Name not in tools and not b.Shape.isNull() and b.Shape.Solids]
    if len(bodies) != 1:
        raise ValueError(f"expected exactly one PartDesign::Body with a solid (excluding Boolean"
                         f" tool bodies {sorted(tools)}), found {len(bodies)}"
                         f" ({[b.Name for b in bodies]})")
    return bodies[0]


def _live_bodies(doc) -> set[str] | None:
    """The result body plus every body it consumes through Booleans (transitively)."""
    try:
        root = _the_body(doc)
    except ValueError:
        return None
    live: set[str] = set()
    todo = [root]
    while todo:
        b = todo.pop()
        if b.Name in live:
            continue
        live.add(b.Name)
        for o in b.Group:
            if o.TypeId == "PartDesign::Boolean" and not getattr(o, "Suppressed", False):
                todo.extend(t for t in (o.Group or []) if t.TypeId == "PartDesign::Body")
    return live


BOOL_KIND = {"Cut": "subtract", "Fuse": "union", "Common": "intersect"}


def _boolean_counts(kind: str, n_body: int, n_tool: int) -> bool:
    """Same rule as the build123d tracer (cross-backend conformance).

    feature_taxonomy: "A boolean that only unites the result of a single feature into the body is
    part of that feature". Subtract: counted iff the tool comprises more than one feature; union /
    intersect: counted iff both operands comprise more than one feature.
    """
    if kind == "subtract":
        return n_body >= 1 and n_tool > 1
    return min(n_body, n_tool) > 1


def _body_shape(body):
    shape = body.Shape.copy()
    shape.Placement = body.getGlobalPlacement()
    return shape


def _write_step(shape, out: str) -> dict:
    """Export a single solid as STEP (mm) and verify by reading it back."""
    import Part

    solids = len(shape.Solids)
    res = {"ok": False, "solids": solids, "volume_mm3": round(shape.Volume, 6),
           "faces": len(shape.Faces)}
    if solids != 1:
        res["error"] = f"canonical export needs exactly one solid, model has {solids}"
        return res
    if not shape.isValid():
        res["error"] = "shape is not a valid B-rep"
        return res
    out_p = Path(out).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    Part.Solid(shape.Solids[0]).exportStep(str(out_p))
    back = Part.read(str(out_p))
    head = out_p.read_text(errors="replace")[:200000]
    unit_mm = "SI_UNIT(.MILLI.,.METRE.)" in head.replace(" ", "")
    res.update(solids=len(back.Solids), volume_mm3=round(back.Volume, 6), faces=len(back.Faces),
               unit="mm" if unit_mm else "unknown", step=str(out_p))
    res["ok"] = len(back.Solids) == 1 and unit_mm
    if not res["ok"]:
        res["error"] = "STEP read-back check failed (solid count or unit)"
    return res


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------------------------
def _parameters(doc) -> list[dict]:
    users: dict[str, list[str]] = {}
    for o in doc.Objects:
        for _prop, expr in (o.ExpressionEngine or []):
            for tok in _expr_refs(expr):
                users.setdefault(tok, []).append(o.Name)
    out = []
    for o in doc.Objects:
        if o.TypeId == "Spreadsheet::Sheet":
            for name, cell in _sheet_aliases(o):
                try:
                    value = float(_jsonable(o.get(cell)))
                except (TypeError, ValueError):
                    continue
                refs = users.get(f"{o.Name}.{name}", []) + (
                    users.get(f"{o.Label}.{name}", []) if o.Label != o.Name else [])
                entry = {"name": name, "value": value}
                if refs:
                    entry["feature"] = ",".join(dict.fromkeys(refs))
                out.append(entry)
        elif o.TypeId == "App::VarSet":
            for p in o.PropertiesList:
                if o.getGroupOfProperty(p) in ("Base", ""):
                    continue
                try:
                    value = float(_jsonable(getattr(o, p)))
                except (TypeError, ValueError):
                    continue
                entry = {"name": p, "value": value}
                refs = users.get(f"{o.Name}.{p}", [])
                if refs:
                    entry["feature"] = ",".join(dict.fromkeys(refs))
                out.append(entry)
    return out


def _expr_refs(expr: str) -> list[str]:
    import re

    return re.findall(r"<<[^>]+>>\.\w+|[A-Za-z_]\w*\.[A-Za-z_]\w*", expr or "")


def _sheet_aliases(sheet) -> list[tuple[str, str]]:
    out = []
    for cell in sheet.getUsedCells():
        alias = sheet.getAlias(cell)
        if alias:
            out.append((alias, cell))
    return out


def extract_doc(doc, native: str) -> dict:
    features: list[dict] = []
    unmapped: list[str] = []
    in_body: set[str] = set()
    sub_transforms: set[str] = set()
    for o in doc.Objects:
        if o.TypeId == MULTI:
            sub_transforms.update(t.Name for t in o.Transformations)

    live = _live_bodies(doc)  # None: no single result body (export fails anyway)
    not_in_final: list[str] = []
    body_n: dict[str, int] = {}  # counted features per body (before boolean folding)
    booleans: list[tuple[dict, str, int, list[str]]] = []  # (feature, kind, n_before, tools)
    for body in _bodies(doc):
        n_local = 0
        in_body.add(body.Name)
        if body.Origin is not None:
            in_body.add(body.Origin.Name)
            in_body.update(f.Name for f in body.Origin.OriginFeatures)
        group = list(body.Group)
        if live is not None and body.Name not in live:
            # scratch body that does not reach the exported solid: informational, never counted
            in_body.update(o.Name for o in group)
            not_in_final.extend(f"{body.Name}/{o.Name}" for o in group
                                if PD_MAP.get(o.TypeId) in COUNTED or o.TypeId == MULTI)
            for o in group:  # dumb geometry is reported wherever it sits (gate fail)
                if o.TypeId not in PD_MAP and o.TypeId not in IGNORED and o.TypeId != MULTI:
                    why = UNMAPPED_PD.get(o.TypeId, "not a native parametric PartDesign feature")
                    unmapped.append(f"{body.Name}/{o.Name} ({o.TypeId}): {why}")
            if body.BaseFeature is not None:
                unmapped.append(f"{body.Name}.BaseFeature={body.BaseFeature.Name} "
                                f"({body.BaseFeature.TypeId}): dumb solid as body base")
            continue
        tip = body.Tip
        if tip is None:
            tip_idx = -1  # no Tip: nothing in this body is part of its shape
        else:
            tip_idx = group.index(tip) if tip in group else len(group) - 1
        for idx, o in enumerate(group):
            in_body.add(o.Name)
            t = o.TypeId
            if t in ("App::Origin",) or o.Name in sub_transforms:
                continue
            if getattr(o, "Suppressed", False):
                log(f"skip suppressed feature {o.Name}")
                continue
            tax = PD_MAP.get(t)
            if t == MULTI:
                if idx > tip_idx:
                    continue
                for sub in o.Transformations:
                    st = PD_MAP.get(sub.TypeId)
                    if st in COUNTED:
                        n_local += 1
                        features.append({"native_name": f"{o.Name}/{sub.Name}",
                                         "native_type": f"{t}/{sub.TypeId}", "taxonomy_type": st,
                                         "counted": True, "params": _feature_params(sub)})
                    else:
                        unmapped.append(f"{o.Name}/{sub.Name} ({sub.TypeId}): "
                                        f"{UNMAPPED_PD.get(sub.TypeId, 'no taxonomy type')}")
                continue
            if tax is None:
                if t in IGNORED:
                    continue
                why = UNMAPPED_PD.get(t, "not a native parametric PartDesign feature")
                unmapped.append(f"{body.Name}/{o.Name} ({t}): {why}")
                continue
            counted = tax in COUNTED
            if counted and idx > tip_idx:
                log(f"skip {o.Name}: after the body Tip, not part of the result")
                continue
            feat = {"native_name": o.Name, "native_type": t, "taxonomy_type": tax,
                    "counted": counted, "params": _feature_params(o)}
            features.append(feat)
            if t == "PartDesign::Boolean":
                tools = [b.Name for b in (o.Group or []) if b.TypeId == "PartDesign::Body"]
                feat["params"]["tools"] = tools
                booleans.append((feat, BOOL_KIND.get(str(o.Type), "union"), n_local, tools))
            if counted:
                n_local += 1
        body_n[body.Name] = n_local
        if body.BaseFeature is not None:
            unmapped.append(f"{body.Name}.BaseFeature={body.BaseFeature.Name} "
                            f"({body.BaseFeature.TypeId}): dumb solid as body base")

    # Fold booleans that only merge a single feature (taxonomy note). A body holding a boolean has
    # >= 2 counted features either way, so folding order does not change any decision.
    for feat, kind, n_before, tools in booleans:
        n_body, counted = n_before, False
        for tname in tools:
            n_tool = body_n.get(tname, 0)
            counted = counted or _boolean_counts(kind, n_body, n_tool)
            if kind == "union":
                n_body += n_tool
        if not tools:
            unmapped.append(f"{feat['native_name']} (PartDesign::Boolean): no tool body")
        if not counted:
            feat["counted"] = False
            merged = [f"{t}:{body_n.get(t, 0)}" for t in tools]
            feat["params"]["merged_into"] = ",".join(merged)
            log(f"boolean {feat['native_name']} only merges single-feature tool(s) {merged}: "
                "part of that feature, not counted")

    for o in doc.Objects:
        if o.Name in in_body or o.TypeId in IGNORED or o.Name in sub_transforms:
            continue
        if o.TypeId in PARAMETER_TYPES:
            features.append({"native_name": o.Name, "native_type": o.TypeId,
                             "taxonomy_type": "parameter", "counted": False})
            continue
        if o.TypeId == "Sketcher::SketchObject":
            features.append({"native_name": o.Name, "native_type": o.TypeId,
                             "taxonomy_type": "sketch", "counted": False})
            continue
        geometric = o.TypeId.split("::")[0] in ("Part", "Mesh", "Points", "PartDesign") or (
            hasattr(o, "Shape") and not o.Shape.isNull())
        if geometric:
            unmapped.append(f"{o.Name} ({o.TypeId}): non-parametric / non-PartDesign geometry")

    doc_out = {
        "tool": TOOL,
        "native_file": os.path.abspath(native),
        "count": sum(1 for f in features if f["counted"]),
        "features": features,
        "unmapped": unmapped,
        "native_sha256": _sha256(native),
        "parameters": _parameters(doc),
    }
    if not_in_final:
        doc_out["not_in_final_part"] = not_in_final
    try:
        doc_out["face_count"] = len(_body_shape(_the_body(doc)).Faces)
    except ValueError as e:
        log(str(e))
    return doc_out


# ---------------------------------------------------------------------------------------------
# set-param
# ---------------------------------------------------------------------------------------------
def _set_parameter(doc, name: str, value: float) -> str:
    sheets = sorted((o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"),
                    key=lambda o: (o.Label != "Params" and o.Name != "Params", o.Name))
    for sheet in sheets:
        for alias, cell in _sheet_aliases(sheet):
            if alias == name:
                sheet.set(cell, repr(float(value)))
                return f"{sheet.Name}.{cell}"
    for o in doc.Objects:
        if o.TypeId == "App::VarSet" and name in o.PropertiesList:
            setattr(o, name, float(value))
            return f"{o.Name}.{name}"
    known = [a for s in sheets for a, _ in _sheet_aliases(s)]
    raise KeyError(f"parameter {name!r} not found (spreadsheet aliases: {known})")


# ---------------------------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------------------------
def cmd_export(a) -> int:
    doc = _open(a.native)
    errs = _errors(doc)
    if errs:
        return emit({"ok": False, "solids": 0, "volume_mm3": 0.0, "faces": 0,
                     "error": f"recompute errors: {errs}"})
    res = _write_step(_body_shape(_the_body(doc)), a.out)
    return emit(res)


def cmd_extract(a) -> int:
    doc = _open(a.native)
    out = extract_doc(doc, a.native)
    errs = _errors(doc)
    Path(a.out).resolve().parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    ok = not errs
    res = {"ok": ok, "count": out["count"], "unmapped": len(out["unmapped"]),
           "features": [f["taxonomy_type"] for f in out["features"] if f["counted"]],
           "out": str(Path(a.out).resolve())}
    if errs:
        res["error"] = f"recompute errors: {errs}"
    return emit(res)


def cmd_set_param(a) -> int:
    doc = _open(a.native)
    before = _body_shape(_the_body(doc)).Volume
    where = _set_parameter(doc, a.name, a.value)
    doc.recompute()
    # FreeCAD 26.3: in a document loaded from disk, a changed Spreadsheet/VarSet value does not
    # re-trigger the expressions bound to it. Touch every expression-driven object so the
    # whole parametric chain is re-evaluated from the new value, then recompute again.
    for o in doc.Objects:
        if o.ExpressionEngine:
            o.touch()
    doc.recompute()
    errs = _errors(doc)
    res = {"ok": False, "volume_before_mm3": round(before, 6), "volume_after_mm3": None,
           "parameter": a.name, "value": a.value, "cell": where}
    if errs:
        res["error"] = f"recompute failed after set-param: {errs}"
        return emit(res)
    step = _write_step(_body_shape(_the_body(doc)), a.out)
    res["volume_after_mm3"] = step["volume_mm3"]
    res["ok"] = step["ok"]
    if not step["ok"]:
        res["error"] = step.get("error")
    if a.native_out:
        Path(a.native_out).resolve().parent.mkdir(parents=True, exist_ok=True)
        doc.saveAs(str(Path(a.native_out).resolve()))
        res["native_out"] = str(Path(a.native_out).resolve())
    return emit(res)


def cmd_replay(a) -> int:
    script = REPLAYS / f"{a.case_id}.py"
    if not script.is_file():
        return emit({"ok": False, "error": f"no replay {script}"})
    out_dir = Path(a.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    native = out_dir / "native.FCStd"
    if native.exists():
        native.unlink()
    env = dict(os.environ)
    import FreeCAD

    env.setdefault("FREECAD_LIB", os.path.dirname(FreeCAD.__file__))
    proc = subprocess.run([sys.executable, str(script), str(native)], cwd=str(REPLAYS), env=env,
                          stdout=sys.stderr, stderr=sys.stderr, timeout=600)
    if proc.returncode != 0 or not native.is_file():
        return emit({"ok": False, "error": f"replay script failed (exit {proc.returncode})"})
    # export + extract read the saved native file back (not the in-memory build)
    doc = _open(str(native))
    errs = _errors(doc)
    step = _write_step(_body_shape(_the_body(doc)), str(out_dir / "output.step"))
    feats = extract_doc(doc, str(native))
    (out_dir / "features.json").write_text(json.dumps(feats, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
    ok = step["ok"] and not errs and not feats["unmapped"]
    res = {"ok": ok, "volume_mm3": step["volume_mm3"], "faces": step["faces"],
           "count": feats["count"], "solids": step["solids"], "out_dir": str(out_dir)}
    if not ok:
        res["error"] = step.get("error") or (f"recompute errors: {errs}" if errs else
                                             f"unmapped: {feats['unmapped']}")
    return emit(res)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Usage errors become a JSON failure (exit 1) instead of argparse's exit 2."""

    def error(self, message: str):  # noqa: D401 - argparse hook
        self.print_usage(sys.stderr)
        raise _UsageError(f"{self.prog}: {message}")


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(prog="recad freecad", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export", help="canonical STEP export of the single Body solid")
    p.add_argument("native")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("extract", help="feature tree -> features.json (taxonomy)")
    p.add_argument("native")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("set-param", help="set a Params alias, recompute, export")
    p.add_argument("native")
    p.add_argument("name")
    p.add_argument("value", type=float)
    p.add_argument("--out", required=True)
    p.add_argument("--native-out")
    p.set_defaults(fn=cmd_set_param)
    p = sub.add_parser("replay", help="run replays/<case_id>.py, then export + extract")
    p.add_argument("case_id")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(fn=cmd_replay)
    try:
        a = ap.parse_args(argv)
    except _UsageError as e:
        return emit({"ok": False, "error": f"usage: {e}"})
    _redirect_stdout()
    try:
        _bootstrap_freecad()
        return a.fn(a)
    except Exception as e:  # noqa: BLE001 - the contract is: one JSON object, exit 1
        traceback.print_exc(file=sys.stderr)
        return emit({"ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    # FreeCAD's start-up deletes its own temporaries (e.g. ``Path``, ``json``) from ``__main__``,
    # so run from this file's *module* namespace, which FreeCAD does not touch.
    sys.path.insert(0, str(HERE))
    import adapter as _adapter

    sys.exit(_adapter.main())
