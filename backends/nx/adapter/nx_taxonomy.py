"""NX feature tree -> ReCADBench taxonomy (spec/recadbench_spec/feature_taxonomy.yaml).

Pure Python (no NXOpen): imported by the NX-side measurement journal (`nx_measure.py`, NX 2206
embedded Python 3.10) and by the CI tests (synthetic rows). Keep it 3.10-compatible.

A *row* is one top-level (not `IsInternal`) feature of the part, in tree order:

    {"ts": int, "name": str, "cls": NX Python class name, "type": Feature.FeatureType,
     "layout": pattern / transform layout name or None, "parents": [ts, ...],
     "suppressed": bool, "live": bool (contributes to the single result solid), "params": {...}}

The mapping was measured on NX 2206 (backends/nx/README.md "Feature mapping"). Classification is by
Python class first (stable across locales and feature names), FeatureType second. Anything not
listed is **unmapped** (gate fail): the counter never guesses.
"""

from __future__ import annotations

COUNTED = {
    "primitive",
    "extrude",
    "revolve",
    "sweep",
    "loft",
    "hole",
    "fillet",
    "chamfer",
    "shell",
    "draft",
    "rib",
    "boolean",
    "pattern_linear",
    "pattern_circular",
    "mirror",
    "thread_cosmetic",
    "freeform_region",
}

# NX Python class name -> taxonomy type (counted or not)
CLASS_MAP = {
    "Extrude": "extrude",
    "Revolve": "revolve",
    "Groove": "revolve",
    "HolePackage": "hole",
    "EdgeBlend": "fillet",
    "FaceBlend": "fillet",
    "Chamfer": "chamfer",
    "Shell": "shell",
    "Draft": "draft",
    "DraftBody": "draft",
    "Rib": "rib",
    "Mirror": "mirror",
    "Block": "primitive",
    "Cylinder": "primitive",
    "Cone": "primitive",
    "Sphere": "primitive",
    "BooleanFeature": "boolean",
    "SweepAlongGuide": "sweep",
    "Swept": "sweep",
    "Tube": "sweep",
    "VariationalSweep": "sweep",
    "ThroughCurves": "loft",
    "ThroughCurveMesh": "loft",
    # not counted
    "SketchFeature": "sketch",
    "DatumPlaneFeature": "datum_plane",
    "DatumCsys": "datum_plane",
    "DatumAxisFeature": "datum_axis",
    "PointFeature": "datum_point",
    "PointSet": "datum_point",
}

# Associative curve features (wireframe): not features of the taxonomy, like sketches.
CURVE_CLASSES = {
    "AssociativeLine",
    "AssociativeArc",
    "CompositeCurve",
    "ProjectCurve",
    "IntersectionCurve",
    "Helix",
    "StudioSpline",
    "FitCurve",
    "BridgeCurve",
    "OffsetCurve",
    "SectionCurve",
    "IsoparametricCurve",
    "CurveOnSurface",
    "JoinCurves",
    "LawCurve",
    "Text",
    "CurveLength",
    "TrimCurve2",
    "MirrorCurve",
    "Wrap",
    "Unwrap",
}
# Organisational rows without geometry of their own: ignored.
IGNORED_CLASSES = {"FeatureGroup"}

# Explicitly NOT parametric features of the taxonomy => unmapped (gate fail), with the reason.
UNMAPPED_CLASSES = {
    "Brep": "unparameterized body (imported geometry or Remove Parameters)",
    "ExtractFace": "extracted / copied body or faces (no design feature)",
    "WaveLinkedBody": "WAVE-linked body from another part",
    "LinkedBody": "linked body from another part",
    "MoveObject": "Move Object on a body (transform of a body, not a design feature)",
    "ScaleBody": "scaled body (no taxonomy type)",
    "OffsetRegion": "synchronous edit (no taxonomy type)",
    "ReplaceFace": "synchronous edit (no taxonomy type)",
    "MoveFace": "synchronous edit (no taxonomy type)",
    "DeleteFace": "synchronous edit (no taxonomy type)",
    "ResizeFace": "synchronous edit (no taxonomy type)",
    "ResizeBlend": "synchronous edit (no taxonomy type)",
    "PullFace": "synchronous edit (no taxonomy type)",
    "TrimBody2": "Trim Body (no taxonomy type)",
    "SplitBody": "Split Body (no taxonomy type)",
}
# FeatureType strings that identify a row whose class is generic (Feature / BodyFeature).
TYPE_MAP = {
    "UNITE": "boolean",
    "SUBTRACT": "boolean",
    "INTERSECT": "boolean",
    "SYMBOLIC_THREAD": "thread_cosmetic",
}
UNMAPPED_TYPES = {
    "BREP": "unparameterized body (imported geometry or Remove Parameters)",
    "UNPARAMETERIZED_FEATURE": "unparameterized body",
    "EXTRACT_BODY": "extracted body copy (no design feature)",
    "LINKED_BODY": "linked body from another part",
}
BOOLEAN_KIND = {"UNITE": "union", "SUBTRACT": "subtract", "INTERSECT": "intersect"}

# Layouts of Pattern Feature / Pattern Geometry / Mirror Geometry (PatternDefinition.PatternEnum,
# GeomcopyBuilder.TransformTypes names) -> taxonomy type; other layouts have none (unmapped).
PATTERN_LAYOUTS = {"Linear": "pattern_linear", "Circular": "pattern_circular", "Mirror": "mirror"}
GEOMCOPY_LAYOUTS = {"Translate": "pattern_linear", "Rotate": "pattern_circular", "Mirror": "mirror"}


def classify(cls: str, ftype: str, layout: str | None = None) -> tuple[str | None, str]:
    """(taxonomy type or None, reason). None => unmapped (reason says why), "" => ignored row."""
    if cls in IGNORED_CLASSES:
        return "", "organisational row"
    if cls == "PatternFeature":
        tax = PATTERN_LAYOUTS.get(layout or "")
        return (tax, "") if tax else (None, f"pattern layout {layout!r} has no taxonomy type")
    if cls == "Geomcopy":
        tax = GEOMCOPY_LAYOUTS.get(layout or "")
        return (tax, "") if tax else (None, f"geometry copy layout {layout!r} has no taxonomy type")
    if cls in UNMAPPED_CLASSES:
        return None, UNMAPPED_CLASSES[cls]
    if cls in CLASS_MAP:
        return CLASS_MAP[cls], ""
    if cls in CURVE_CLASSES:
        return "sketch", ""
    if ftype in UNMAPPED_TYPES:
        return None, UNMAPPED_TYPES[ftype]
    if ftype in TYPE_MAP:
        return TYPE_MAP[ftype], ""
    return (
        None,
        f"unknown NX feature (class {cls}, type {ftype}): not a recognised parametric feature",
    )


def boolean_counts(kind: str, n_body: int, n_tool: int) -> bool:
    """Same rule as the build123d tracer and the FreeCAD adapter (cross-backend conformance).

    "A boolean that only unites the result of a single feature into the body is part of that
    feature": subtract is counted iff the tool comprises more than one feature; union / intersect
    iff both operands comprise more than one feature."""
    if kind == "subtract":
        return n_body >= 1 and n_tool > 1
    return min(n_body, n_tool) > 1


def _closure(start: list[int], parents: dict[int, list[int]]) -> set[int]:
    seen: set[int] = set()
    todo = list(start)
    while todo:
        t = todo.pop()
        if t in seen:
            continue
        seen.add(t)
        todo.extend(parents.get(t, []))
    return seen


def build(rows: list[dict]) -> dict:
    """features[], unmapped[], not_in_final_part[], count from the rows (features.json body)."""
    features: list[dict] = []
    unmapped: list[str] = []
    not_in_final: list[str] = []
    parents = {r["ts"]: list(r.get("parents") or []) for r in rows}
    tax_of: dict[int, str] = {}
    for r in rows:
        tax, why = classify(r["cls"], r["type"], r.get("layout"))
        label = f"{r['name']} ({r['cls']}/{r['type']})"
        if r.get("suppressed"):
            continue  # inactive rows are not part of the model (suppressed or rolled back)
        if tax is None:
            unmapped.append(f"{label}: {why}")
            continue
        if tax == "":
            continue
        tax_of[r["ts"]] = tax
        counted = tax in COUNTED
        if counted and not r.get("live", True):
            not_in_final.append(label)
            continue
        feat = {
            "native_name": r["name"],
            "native_type": f"{r['cls']}/{r['type']}",
            "taxonomy_type": tax,
            "counted": counted,
        }
        if r.get("params"):
            feat["params"] = dict(r["params"])
        if r.get("layout"):
            feat.setdefault("params", {})["layout"] = r["layout"]
        feat["_ts"] = r["ts"]
        features.append(feat)

    # Fold booleans that only merge a single feature (taxonomy note). NX lists the target body's
    # feature first in Feature.GetParents() and the tool feature(s) after it (measured). Both
    # operands are measured the same way: the counted features in the parent closure of that
    # operand. A body that merely exists earlier in the tree (not an ancestor of the target) is
    # not part of the operand. Booleans are folded in tree order and a folded boolean no longer
    # counts as a feature of later operands (same as the build123d tracer).
    counted_ts = {f["_ts"] for f in features if f["counted"]}
    for f in sorted(features, key=lambda g: g["_ts"]):
        if f["taxonomy_type"] != "boolean":
            continue
        row = next(r for r in rows if r["ts"] == f["_ts"])
        kind = BOOLEAN_KIND.get(row["type"], "union")
        ps = parents.get(row["ts"], [])
        target, tools = ps[:1], ps[1:]
        body_set = _closure(target, parents)
        tool_set = _closure(tools, parents) - body_set
        n_tool = len(tool_set & counted_ts)
        n_body = len(body_set & counted_ts)
        f.setdefault("params", {}).update(kind=kind, n_body=n_body, n_tool=n_tool)
        if not tools:
            unmapped.append(f"{f['native_name']} (BooleanFeature): no tool feature found")
        elif not boolean_counts(kind, n_body, n_tool):
            f["counted"] = False
            f["params"]["merged_into"] = "single-feature operand: part of that feature"
            counted_ts.discard(f["_ts"])
    for f in features:
        f.pop("_ts", None)
    out = {
        "features": features,
        "unmapped": unmapped,
        "count": sum(1 for f in features if f["counted"]),
    }
    if not_in_final:
        out["not_in_final_part"] = not_in_final
    return out
