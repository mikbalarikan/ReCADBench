"""S2 parsimony (25, anti-LEGO). 03_SCORING §3 S2.

``r = N_pred / N_ref`` with ``N_pred`` = the number of ``features.json`` rows with
``counted: true`` (extracted by the adapter on the worker, never self-reported; recomputed here
rather than read from the derived ``count`` field) and ``N_ref`` = ``meta.yaml: n_ref``.

- 0.7 <= r <= 1.2 -> 25; 1.2 < r <= 3 -> linear to 0; r > 3 -> 0 and the total is capped at 40;
- 0.5 <= r < 0.7 -> linear 25 -> 10; r < 0.5 -> 0;
- ``face_ratio = faces_pred / faces_gt > 2`` -> S2 halved;
- parametric presence: each GT key dim must map to >= 1 native parameter (``parameters[]``
  values or numeric feature ``params``; a diameter may be driven by a radius and vice versa)
  matching the candidate's measured value or the GT nominal within the class tolerance;
  coverage < 50% -> S2 halved.

Open layer (ADR 0006): ``s2_face`` scores ``q = faces_pred / faces_gt`` instead (no features).
"""

from __future__ import annotations

from collections.abc import Iterable

from .config import EvalConfig


def ratio_curve(r: float, full: float = 25.0) -> float:
    """The S2 curve, scaled to ``full`` points (plan score reuses it with 15)."""
    if 0.7 <= r <= 1.2:
        v = 25.0
    elif 1.2 < r <= 3.0:
        v = 25.0 * (3.0 - r) / 1.8
    elif r > 3.0:
        v = 0.0
    elif 0.5 <= r < 0.7:
        v = 10.0 + 15.0 * (r - 0.5) / 0.2
    else:
        v = 0.0
    return v * full / 25.0


def _numbers(x) -> Iterable[float]:
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        yield float(x)
    elif isinstance(x, dict):
        for v in x.values():
            yield from _numbers(v)
    elif isinstance(x, list):
        for v in x:
            yield from _numbers(v)


def native_values(features: dict) -> list[float]:
    vals = [float(p["value"]) for p in features.get("parameters") or []
            if isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool)]
    for f in features.get("features") or []:
        vals.extend(_numbers(f.get("params") or {}))
    return vals


def covered(dim_kind: str, targets: list[float], values: list[float], tol: float) -> bool:
    scales = (1.0, 2.0, 0.5) if dim_kind in ("diameter", "radius", "axis_distance") else (1.0,)
    for t in targets:
        for v in values:
            for s in scales:
                if abs(v * s - t) <= tol + 1e-9:
                    return True
    return False


def param_coverage(dims, features: dict, tol: float) -> tuple[float, list[str]]:
    """Share of key dims driven by a native parameter; also returns the uncovered names."""
    values = native_values(features)
    missing = []
    for d in dims:
        targets = [d.nominal] + ([d.measured] if d.measured is not None else [])
        if not covered(d.kind, targets, values, tol):
            missing.append(d.name)
    n = len(dims)
    return ((n - len(missing)) / n if n else 1.0), missing


def s2(features: dict, n_ref: int, faces_pred: int, faces_gt: int, dims, tol: float,
       cfg: EvalConfig) -> dict:
    n_pred = sum(1 for f in features.get("features") or [] if f.get("counted") is True)
    r = n_pred / n_ref if n_ref else 0.0
    score = ratio_curve(r)
    face_ratio = faces_pred / faces_gt if faces_gt else 0.0
    halved = []
    if face_ratio > cfg.face_ratio_halve:
        halved.append("face_ratio")
    cov, missing = param_coverage(dims, features, tol)
    if cov < cfg.param_coverage_min:
        halved.append("param_coverage")
    for _ in halved:
        score *= 0.5
    return {
        "S2": score,
        "r_feat": r,
        "n_pred": n_pred,
        "n_ref": n_ref,
        "face_ratio": face_ratio,
        "param_coverage": cov,
        "uncovered_key_dims": missing,
        "halved_by": halved,
        "cap_total": r > 3.0,
    }


def s2_face(faces_pred: int, faces_gt: int) -> dict:
    """S2-face (Open layer, ADR 0006 section 2): the parsimony proxy for STEP-only submissions.

    ``q = faces_pred / faces_gt`` mapped to a STEP curve (no linear decay): a continuous curve
    would let anyone invert the public S2 (or total) back to the GT face count. Steps, calibrated
    on the 46 gate-ok P11 pilot runs (agents usually leave out GT fillets/chamfers, so
    q ~ 0.55 r on Track E): [0.35, 1.5] -> 25, (1.5, 2] -> 15, (2, 3] -> 5, [0.25, 0.35) -> 10,
    else 0 (mean |S2-face - S2| = 1.9 over all runs, 5.1 on Track E). The public report shows
    the same buckets. No param-coverage or face-ratio halving; ``q > 3`` caps the total
    (``total_cap_lego``). Provisional: recalibrate with the broadcast runs.
    """
    q = faces_pred / faces_gt if faces_gt else 0.0
    if 0.35 <= q <= 1.5:
        v = 25.0
    elif 1.5 < q <= 2.0:
        v = 15.0
    elif 2.0 < q <= 3.0:
        v = 5.0
    elif 0.25 <= q < 0.35:
        v = 10.0
    else:
        v = 0.0
    return {"S2": v, "face_ratio": q, "cap_total": q > 3.0, "provisional": True}
