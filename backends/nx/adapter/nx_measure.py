"""NX-side measurement helpers (run INSIDE NX 2206 via run_journal.exe, Python 3.10).

Used by `runner/nx_wrapper.py` (after every `recadbench nx-run`) and by `nx_adapter_journal.py`
(`recad nx export|extract|set-param|replay`). This is the measurement instrument (00_DECISIONS D3):
it lives in the benchmark repo so a skillset cannot be tuned against it. Ported from the
measurement half of the owner's NX helper modules (feature table, user expressions, bodies report,
mass properties, STEP export) - see backends/nx/README.md "Provenance".
"""

from __future__ import annotations

import json
import os
import re
import time

import NXOpen
import NXOpen.Features
import NXOpen.GeometricUtilities

import nx_taxonomy

S = NXOpen.Session.GetSession()
TEMPLATE = "model-plain-1-mm-template.prt"

# Expression names NX writes itself (p12, p12_x, Pattern_..., ASSO_BLEND_3, Sheet_Metal_...).
# Only a fallback: an expression with IsUserSpecified is a user parameter whatever its name.
SYSTEM_EXPR_NAME = re.compile(
    r"^(p\d+(_\w+)?|Pattern_.*|ASSO_BLEND_\d+|Sheet_Metal_\w+|SM_Validation_\w+)$"
)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, default=str)
        fh.write("\n")


# ---------------------------------------------------------------- parts
def new_part(path):
    """New mm part from the NX plain template (work + display part). Not written until saved."""
    if os.path.exists(path):
        os.remove(path)
    b = S.Parts.FileNew()
    try:
        b.TemplateFileName = TEMPLATE
        b.Units = NXOpen.Part.Units.Millimeters
        b.NewFileName = path
        b.DisplayPartOption = NXOpen.DisplayPartOption.AllowAdditional
        b.Commit()
    finally:
        b.Destroy()
    part = S.Parts.Work
    try:
        part.Preferences.Modeling.DistanceToleranceData = 0.01
    except Exception:  # noqa: BLE001
        pass
    return part


def open_part(path):
    part, st = S.Parts.OpenBaseDisplay(path)
    st.Dispose()
    return part


def save(part):
    part.Save(
        NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.FalseValue
    ).Dispose()


def save_as(part, path):
    if os.path.exists(path):
        os.remove(path)
    part.SaveAs(path).Dispose()


def update():
    S.UpdateManager.SetDefaultUpdateFailureAction(NXOpen.Update.FailureOption.AcceptAll)
    m = S.SetUndoMark(NXOpen.Session.MarkVisibility.Invisible, "recadbench update")
    return S.UpdateManager.DoUpdate(m)


def feature_errors(part):
    """{feature name: [messages]}; flagged features are re-evaluated once, because NX re-attaches
    the message of a rolled-back failed commit to the next feature created (a stale message)."""

    def scan():
        out, flagged = {}, []
        for f in part.Features.GetFeatures():
            try:
                msgs = list(f.GetFeatureErrorMessages() or [])
            except Exception:  # noqa: BLE001
                msgs = []
            if msgs:
                out[f.GetFeatureName()] = msgs
                flagged.append(f)
        return out, flagged

    out, flagged = scan()
    if out:
        for f in flagged:
            try:
                S.UpdateManager.LogForUpdate(f)
            except Exception:  # noqa: BLE001
                pass
        update()
        out, _ = scan()
    return out


# ---------------------------------------------------------------- bodies
def solids(part):
    return [b for b in part.Bodies if b.IsSolidBody]


def sheets(part):
    return [b for b in part.Bodies if not b.IsSolidBody]


def volume(part, bodies):
    bodies = [b for b in bodies if b.IsSolidBody]
    if not bodies:
        return 0.0
    uc = part.UnitCollection
    un = [
        uc.FindObject(u)
        for u in ("SquareMilliMeter", "CubicMilliMeter", "Kilogram", "MilliMeter", "Newton")
    ]
    mb = part.MeasureManager.NewMassProperties(un, 0.999999, bodies)
    try:
        return float(mb.Volume)
    finally:
        mb.Dispose()


def imported_facet_bodies(part):
    """Facet bodies that are not the display facets of a body of the part (measured on NX 2206).

    NX keeps one FacetedBody per solid/sheet for display, with `AssociatedBody` = that body. An
    STL imported as a JT-type facet body (or any other mesh object) has no associated body and
    no feature row, so the feature tree alone does not see it: it is reported as unmapped."""
    tags = {b.Tag for b in part.Bodies}
    out = []
    try:
        fbs = list(part.FacetedBodies)
    except Exception:  # noqa: BLE001
        return out
    for fb in fbs:
        try:
            assoc = fb.AssociatedBody
        except Exception:  # noqa: BLE001
            assoc = None
        if assoc is not None and getattr(assoc, "Tag", None) in tags:
            continue
        try:
            jid = fb.JournalIdentifier
        except Exception:  # noqa: BLE001
            jid = str(fb.Tag)
        out.append(jid)
    return out


def facet_unmapped(part):
    return [
        f"facet body {jid}: imported mesh (facet body without a parametric body), not a design "
        "feature"
        for jid in imported_facet_bodies(part)
    ]


def _body_name(b):
    """Journal identifier of a body (e.g. "EXTRUDE(1)"); its tag when NX gives none."""
    try:
        return str(b.JournalIdentifier)
    except Exception:  # noqa: BLE001
        return str(b.Tag)


def bodies_summary(part):
    sol = solids(part)
    out = {"bodies": len(sol), "sheets": len(sheets(part)), "volume": round(volume(part, sol), 6)}
    out["facet_bodies"] = len(imported_facet_bodies(part))
    if len(sol) == 1:
        out["faces"] = len(sol[0].GetFaces())
    return out


# ---------------------------------------------------------------- feature tree
def _internal(f):
    try:
        return bool(f.IsInternal)
    except Exception:  # noqa: BLE001
        return False


def _enum_name(enum_cls, value):
    for n in dir(enum_cls):
        if n.startswith("_"):
            continue
        try:
            if getattr(enum_cls, n) == value:
                return n
        except Exception:  # noqa: BLE001
            pass
    return str(value)


def _layout(part, f):
    """Layout of a Pattern Feature / Geometry copy, read from its edit builder (destroyed again)."""
    cls = type(f).__name__
    try:
        if cls == "PatternFeature":
            b = part.Features.CreatePatternFeatureBuilder(f)
            try:
                return _enum_name(
                    NXOpen.GeometricUtilities.PatternDefinition.PatternEnum,
                    b.PatternService.PatternType,
                )
            finally:
                b.Destroy()
        if cls == "Geomcopy":
            b = part.Features.CreateGeomcopyBuilder(f)
            try:
                return _enum_name(NXOpen.Features.GeomcopyBuilder.TransformTypes, b.Type)
            finally:
                b.Destroy()
    except Exception as e:  # noqa: BLE001
        return "unreadable: " + str(e)[:80]
    return None


def _suppressed(f):
    try:
        return bool(f.Suppressed)
    except Exception:  # noqa: BLE001
        return False


def is_user_expression(e):
    """True for an expression a person / journal created (Expressions.CreateWithUnits ...).

    NX 2206 sets `Expression.IsUserSpecified` for exactly those (measured: also after save and
    reopen), so a user name that looks like an NX name (`p1_width`, `Pattern_pitch`) is kept. The
    name test stays as a fallback for names that do not look like NX's own (for example a
    feature expression renamed by the author)."""
    try:
        if bool(e.IsUserSpecified):
            return True
    except Exception:  # noqa: BLE001
        pass
    return not SYSTEM_EXPR_NAME.match(e.Name)


def user_expressions(part):
    return [e for e in part.Expressions if is_user_expression(e)]


def _feature_exprs(f):
    try:
        return {e.Name: e.RightHandSide for e in f.GetExpressions()}
    except Exception:  # noqa: BLE001
        return {}


def rows(part):
    """Top-level feature rows for nx_taxonomy.build (tree order)."""
    sol = solids(part)
    final_ts = None
    if len(sol) == 1:
        try:
            final_ts = {f.Timestamp for f in sol[0].GetFeatures()}
        except Exception:  # noqa: BLE001
            final_ts = None
    out = []
    for f in part.Features.GetFeatures():
        if _internal(f):
            continue
        try:
            par = [p.Timestamp for p in f.GetParents()]  # keep NX order: target first
        except Exception:  # noqa: BLE001
            par = []
        exprs = _feature_exprs(f)
        out.append(
            {
                "ts": f.Timestamp,
                "name": f.GetFeatureName(),
                "cls": type(f).__name__,
                "type": f.FeatureType,
                "layout": _layout(part, f),
                "parents": par,
                "suppressed": _suppressed(f),
                "live": True if final_ts is None else f.Timestamp in final_ts,
                "params": {"expressions": exprs} if exprs else {},
            }
        )
    return out


def parameters(part):
    users = {}
    for f in part.Features.GetFeatures():
        if _internal(f):
            continue
        for _n, rhs in _feature_exprs(f).items():
            for tok in set(re.findall(r"[A-Za-z_]\w*", rhs or "")):
                users.setdefault(tok, []).append(f.GetFeatureName())
    out = []
    for e in user_expressions(part):
        try:
            v = float(e.Value)
        except Exception:  # noqa: BLE001
            continue
        entry = {"name": e.Name, "value": v}
        if users.get(e.Name):
            entry["feature"] = ",".join(dict.fromkeys(users[e.Name]))
        out.append(entry)
    return out


def components(part):
    """Assembly components in the part: geometry that lives in another part file => unmapped."""
    try:
        root = part.ComponentAssembly.RootComponent
        kids = list(root.GetChildren()) if root is not None else []
    except Exception:  # noqa: BLE001
        kids = []
    return [
        f"component {k.DisplayName}: geometry from another part (assembly / imported STEP assembly)"
        for k in kids
    ]


def extract(part, native_path):
    """features.json content (without native_sha256, which the host adds)."""
    tree = nx_taxonomy.build(rows(part))
    tree["unmapped"].extend(components(part))
    tree["unmapped"].extend(facet_unmapped(part))
    doc = {
        "tool": "nx",
        "native_file": os.path.abspath(native_path),
        "count": tree["count"],
        "features": tree["features"],
        "unmapped": tree["unmapped"],
        "parameters": parameters(part),
    }
    if tree.get("not_in_final_part"):
        doc["not_in_final_part"] = tree["not_in_final_part"]
    if tree.get("excluded"):
        doc["excluded"] = tree["excluded"]
    summ = bodies_summary(part)
    if "faces" in summ:
        doc["face_count"] = summ["faces"]
    # spec 0.3.0 (ADR 0004 section 4): `bodies` = names of the solid bodies (informational)
    doc["bodies"] = [_body_name(b) for b in solids(part)]
    if len(doc["bodies"]) == 1:
        doc["final_part"] = f"the single solid body {doc['bodies'][0]}"
    doc["sheets"] = summ["sheets"]
    doc["facet_bodies"] = summ["facet_bodies"]
    return doc


# ---------------------------------------------------------------- expressions / STEP
def set_parameter(part, name, value):
    try:
        e = part.Expressions.FindObject(name)
    except Exception:  # noqa: BLE001
        e = None
    if e is None:
        raise KeyError(
            f"expression {name!r} not found (user expressions: "
            f"{[x.Name for x in user_expressions(part)]})"
        )
    old = e.RightHandSide
    e.RightHandSide = repr(float(value))
    return old


def step_export(part, dest, timeout=180):
    """AP242 export of the SAVED part file's solids; waits until the file stops growing."""
    if os.path.exists(dest):
        os.remove(dest)
    b = S.DexManager.CreateStepCreator()
    try:
        b.ExportAs = NXOpen.StepCreator.ExportAsOption.Ap242
        b.InputFile = part.FullPath
        b.OutputFile = dest
        b.LayerMask = "1-256"
        b.ObjectTypes.Solids = True
        b.FileSaveFlag = False
        b.ProcessHoldFlag = True
        b.BsplineTol = 0.001
        b.Commit()
    finally:
        b.Destroy()
    t0, last = time.time(), -1
    while time.time() - t0 < timeout:
        size = os.path.getsize(dest) if os.path.exists(dest) else -1
        if size > 0 and size == last:
            break
        last = size
        time.sleep(0.5)
    if not os.path.exists(dest):
        raise RuntimeError("STEP translator wrote no file: " + dest)
    with open(dest, encoding="utf-8", errors="replace") as fh:
        head = fh.read(400000).replace(" ", "").upper()
    return {"step": dest, "unit": "mm" if "SI_UNIT(.MILLI.,.METRE.)" in head else "unknown"}
