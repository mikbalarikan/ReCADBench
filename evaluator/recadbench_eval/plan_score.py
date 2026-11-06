"""Plan score: RE Plan IR vs ``gt_plan.json`` (100, reported separately). 03_SCORING §5.

| Component | Pts | Method |
|---|---|---|
| Feature multiset match | 30 | Hungarian matching on (type, key params) of counted features; F1 |
| Key-dim values in plan | 30 | key-dim nominals among the IR numbers (class tolerance) |
| Datums | 15 | primary datum kind (5) + normal/axis within 2 deg of the GT's (10) |
| Feature count ratio | 15 | the S2 curve on counted IR features / counted GT features |
| Order plausibility | 10 | share of features whose refs resolve to earlier ids and that do |
|   |   | not modify a body before one exists |

Frames: the IR is in the input frame (the scanner frame for scans, ADR 0002); its datum
directions are rotated into the GT frame with ``T_scan_to_gt`` before comparison. Key dims are
intrinsic values, so they need no mapping. A schema-invalid IR scores 0.

Also: ``ir_adherence`` (counted-type multiset F1 between gt_plan and an extracted features.json)
and ``plan_drift`` (1 - that F1 between the first and the final IR).

Importable on its own (strategies ``eval_local`` use it; gated on ``RECADBENCH_GT_DIR``).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from .parsimony import ratio_curve

BODY_MAKERS = {"primitive", "extrude", "revolve", "sweep", "loft", "rib"}
NEEDS_BODY = {"fillet", "chamfer", "shell", "draft", "hole", "pattern_linear",
              "pattern_circular", "mirror", "boolean", "thread_cosmetic"}
SKETCH_SIZE_KEYS = ("radius", "width", "height", "diameter")


def _taxonomy():
    from recadbench_spec import load_taxonomy

    return load_taxonomy()


def _counted(plan: dict, tax=None) -> list[dict]:
    tax = tax or _taxonomy()
    return [f for f in plan.get("features") or [] if tax.is_counted(f.get("type", ""))]


def _params(plan: dict) -> dict[str, float]:
    out = {}
    for p in plan.get("parameters") or []:
        v = p.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[p["name"]] = float(v)
    return out


def _num(v: Any, params: dict[str, float]) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict) and set(v) == {"ref"}:
        return params.get(v["ref"])
    return None


def scalar_params(feature: dict, params: dict[str, float]) -> dict[str, float]:
    out = {}
    for k, v in (feature.get("params") or {}).items():
        x = _num(v, params)
        if x is not None:
            out[k] = x
    return out


def plan_numbers(plan: dict) -> list[float]:
    """Numbers an IR states as design values: parameters, scalar feature params, sketch sizes."""
    params = _params(plan)
    vals = list(params.values())
    for f in plan.get("features") or []:
        vals.extend(scalar_params(f, params).values())
        for e in (f.get("sketch") or {}).get("entities") or []:
            for k in SKETCH_SIZE_KEYS:
                x = _num(e.get(k), params)
                if x is not None:
                    vals.append(x)
    return vals


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05 * abs(b), 0.1)


def _similarity(fi: dict, fj: dict, pi: dict, pj: dict) -> float:
    if fi.get("type") != fj.get("type"):
        return 0.0
    a, b = scalar_params(fi, pi), scalar_params(fj, pj)
    common = sorted(set(a) & set(b))
    frac = sum(_close(a[k], b[k]) for k in common) / len(common) if common else 1.0
    return 0.5 + 0.5 * frac


def multiset_f1(ir: dict, gt: dict, tax=None) -> tuple[float, list[tuple[str, str, float]]]:
    fi, fg = _counted(ir, tax), _counted(gt, tax)
    if not fi or not fg:
        return 0.0, []
    pi, pg = _params(ir), _params(gt)
    S = np.array([[_similarity(a, b, pi, pg) for b in fg] for a in fi])
    rows, cols = linear_sum_assignment(-S)
    pairs = [(fi[r]["id"], fg[c]["id"], float(S[r, c])) for r, c in zip(rows, cols, strict=True)
             if S[r, c] > 0]
    tp = sum(s for *_, s in pairs)
    p, r = tp / len(fi), tp / len(fg)
    return (0.0 if p + r == 0 else 2 * p * r / (p + r)), pairs


def key_dims_in_plan(ir: dict, key_dims: dict, tol: float) -> tuple[float, list[str]]:
    vals = plan_numbers(ir)
    dims = key_dims.get("dims") or []
    missing = []
    for d in dims:
        nom = float(d["nominal"])
        scales = (1.0, 2.0, 0.5) if d["kind"] in ("diameter", "radius", "axis_distance") else (1.0,)
        if not any(abs(v * s - nom) <= tol + 1e-9 for v in vals for s in scales):
            missing.append(d["name"])
    return ((len(dims) - len(missing)) / len(dims) if dims else 1.0), missing


def _primary(plan: dict) -> dict | None:
    for d in plan.get("datums") or []:
        if d.get("role") == "primary":
            return d
    return None


def datum_score(ir: dict, gt: dict, R_scan_to_gt: np.ndarray, tol_deg: float = 2.0
                ) -> tuple[float, dict]:
    di, dg = _primary(ir), _primary(gt)
    if di is None or dg is None:
        return 0.0, {"note": "no primary datum"}
    kind_ok = di.get("kind") == dg.get("kind")
    ang = None
    if kind_ok and di.get("direction") is not None and dg.get("direction") is not None:
        a = R_scan_to_gt @ np.asarray(di["direction"], dtype=float)
        b = np.asarray(dg["direction"], dtype=float)
        c = abs(float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))))
        ang = math.degrees(math.acos(min(1.0, c)))
    pts = (5.0 if kind_ok else 0.0) + (10.0 if ang is not None and ang <= tol_deg else 0.0)
    return pts, {"kind_ok": kind_ok, "angle_deg": ang}


def _ref_ids(feature: dict) -> list[str]:
    out: list[str] = []

    def walk(x):
        if isinstance(x, str):
            out.append(x.split(":", 1)[0])
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for k, v in (feature.get("params") or {}).items():
        if k in ("features", "tool_feature", "edges", "faces", "open_faces", "neutral", "axis",
                 "plane", "path", "sections", "direction", "direction2"):
            walk(v)
    walk(feature.get("refs") or [])
    plane = (feature.get("sketch") or {}).get("plane")
    if isinstance(plane, str):
        out.append(plane.split(":", 1)[0])
    return out


def order_plausibility(ir: dict) -> tuple[float, list[str]]:
    known = {d["id"] for d in ir.get("datums") or []} | {
        p["id"] for p in ir.get("reference_planes") or []}
    feats = ir.get("features") or []
    bad, body = [], False
    for f in feats:
        ok = all(r in known for r in _ref_ids(f))
        if f.get("type") in NEEDS_BODY and not body:
            ok = False
        if f.get("type") in BODY_MAKERS:
            body = True
        if not ok:
            bad.append(f.get("id", "?"))
        known.add(f.get("id", ""))
    return ((len(feats) - len(bad)) / len(feats) if feats else 0.0), bad


def type_f1(a: Counter, b: Counter) -> float:
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        return 0.0
    tp = sum((a & b).values())
    p, r = tp / na, tp / nb
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def ir_adherence(gt_plan: dict, features: dict, tax=None) -> float:
    tax = tax or _taxonomy()
    a = Counter(f["type"] for f in _counted(gt_plan, tax))
    b = Counter(f["taxonomy_type"] for f in features.get("features") or [] if f.get("counted"))
    return type_f1(a, b)


def plan_drift(first: dict, final: dict, tax=None) -> float:
    tax = tax or _taxonomy()
    a = Counter(f["type"] for f in _counted(first, tax))
    b = Counter(f["type"] for f in _counted(final, tax))
    return 1.0 - type_f1(a, b)


def plan_score(ir: dict, gt_plan: dict, key_dims: dict, tol: float,
               T_scan_to_gt: np.ndarray | None = None) -> dict:
    """Score one IR against the GT plan. Returns ``{"plan_score": .., components, detail}``."""
    from recadbench_spec import validate_document

    rep = validate_document(ir, "re_plan_ir")
    if not rep.ok:
        errs = [str(e) for e in rep.errors][:5]
        return {"plan_score": 0.0, "valid": False, "errors": errs}
    tax = _taxonomy()
    R = np.eye(3) if T_scan_to_gt is None else np.asarray(T_scan_to_gt, dtype=float)[:3, :3]
    f1, pairs = multiset_f1(ir, gt_plan, tax)
    kd, kd_missing = key_dims_in_plan(ir, key_dims, tol)
    dat, dat_detail = datum_score(ir, gt_plan, R)
    n_ir, n_gt = len(_counted(ir, tax)), len(_counted(gt_plan, tax))
    r = n_ir / n_gt if n_gt else 0.0
    order, bad = order_plausibility(ir)
    comp = {
        "feature_match": 30.0 * f1,
        "key_dims": 30.0 * kd,
        "datums": dat,
        "count_ratio": ratio_curve(r, 15.0),
        "order": 10.0 * order,
    }
    return {
        "plan_score": sum(comp.values()),
        "valid": True,
        "components": comp,
        "feature_f1": f1,
        "matched": [list(p) for p in pairs],
        "key_dims_found": kd,
        "key_dims_missing": kd_missing,
        "datum": dat_detail,
        "r_plan": r,
        "order_violations": bad,
    }
