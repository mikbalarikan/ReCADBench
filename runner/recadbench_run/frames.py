"""Rigid frame mapping of an RE Plan IR (Track T seed plan, 00_DECISIONS D4 / HANDOVER section 3.5).

``gt_plan.json`` is authored in the gt.step frame; an agent works in the scanner frame (the frame
of ``input/input.stl``, ADR 0002). ``registration.T_scan_to_gt`` maps scanner coordinates into
the GT frame (``p_gt = R p_scan + t``), so the seed plan an agent receives is the GT plan mapped
by ``inv(T_scan_to_gt)``.

What moves (driven by the taxonomy param types, spec 0.3.0, not by a hard-coded key list):

| IR field | rule |
|---|---|
| ``datums[].origin`` | point: ``R p + t`` |
| ``datums[].direction``, ``datums[].x_dir`` | vector: ``R v`` |
| ``reference_planes[].origin`` / ``normal`` / ``x_dir`` | point / vector / vector |
| param of type ``vec3`` (``primitive.center``) | point |
| param of type ``vec3_list`` (``hole.positions``) | points |
| param of type ``direction`` given as ``[x, y, z]`` | vector |
| face selector ``<F>:<type>:<+/-X|Y|Z>:<ext>[:n]`` | axis token rewritten if still a frame axis |

What stays: sketch coordinates (local to their plane, which moves with the frame), scalars,
parameters, ids, edge-set selectors (``<F>:edges:<filter>``), ``axis`` / ``any`` selectors.

A face selector names a world axis (``BODY:plane:+Z:max``). Under a general rotation that axis is
no longer a frame axis, and spec 0.3.0 has no selector form for an arbitrary direction (STATUS
open question: datum-relative selectors need an ADR). Such a selector keeps its feature and face
type, its direction and extremum become ``any`` and the feature's ``evidence.note`` records the
exact mapped direction, so the meaning is kept for the agent; :func:`map_plan` reports each one.

Pure Python (no numpy), deterministic; coordinates are rounded to 1e-9 mm (avoids -0.0 and 1e-17
noise, far below every tolerance; rounding to 1e-6 made sliver solids in the H7 dry plans).
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Sequence
from typing import Any

Matrix = list[list[float]]

DIGITS = 9
AXIS_TOL = 1e-9  # |component| this close to 1 means "is a frame axis"
_AXES = {"X": 0, "Y": 1, "Z": 2}
_FACE_SEL = re.compile(
    r"^(?P<fid>[A-Za-z][A-Za-z0-9_]*):(?P<kind>plane|cylinder|cone|sphere|torus|bspline|any):"
    r"(?P<dir>[+-][XYZ]|axis|any):(?P<ext>max|min|any)(?P<n>:[0-9]+)?$"
)


# ------------------------------------------------------------------------------ rigid algebra
def as_matrix(T: Sequence[Sequence[float]]) -> Matrix:
    m = [[float(v) for v in row] for row in T]
    if len(m) != 4 or any(len(r) != 4 for r in m):
        raise ValueError("T must be a 4x4 matrix")
    return m


def rigid_problems(T: Sequence[Sequence[float]], tol: float = 1e-6) -> list[str]:
    """Why ``T`` is not a proper rigid transform (empty list = it is one)."""
    m = as_matrix(T)
    out = []
    if any(abs(m[3][j] - (1.0 if j == 3 else 0.0)) > tol for j in range(4)):
        out.append("last row must be [0, 0, 0, 1]")
    R = [row[:3] for row in m[:3]]
    for i in range(3):
        for j in range(3):
            dot = sum(R[k][i] * R[k][j] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > tol:
                out.append("rotation part is not orthonormal")
                return out
    if det3(R) < 0:
        out.append("rotation part is a reflection (det < 0)")
    return out


def det3(R: Sequence[Sequence[float]]) -> float:
    return (R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
            - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
            + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0]))


def invert_rigid(T: Sequence[Sequence[float]]) -> Matrix:
    """``inv(T)`` for a rigid ``T = [R t; 0 1]``: ``[R^T, -R^T t; 0 1]``."""
    m = as_matrix(T)
    probs = rigid_problems(m)
    if probs:
        raise ValueError("not a rigid transform: " + "; ".join(probs))
    R = [row[:3] for row in m[:3]]
    t = [m[i][3] for i in range(3)]
    Rt = [[R[j][i] for j in range(3)] for i in range(3)]
    ti = [-sum(Rt[i][k] * t[k] for k in range(3)) for i in range(3)]
    return [Rt[0] + [ti[0]], Rt[1] + [ti[1]], Rt[2] + [ti[2]], [0.0, 0.0, 0.0, 1.0]]


def _r(x: float) -> float:
    v = round(float(x), DIGITS)
    return 0.0 if v == 0 else v


def apply_point(T: Matrix, p: Sequence[float]) -> list[float]:
    return [_r(sum(T[i][k] * float(p[k]) for k in range(3)) + T[i][3]) for i in range(3)]


def apply_vector(T: Matrix, v: Sequence[float]) -> list[float]:
    return [_r(sum(T[i][k] * float(v[k]) for k in range(3))) for i in range(3)]


def _is_vec3(v: Any) -> bool:
    return (isinstance(v, list) and len(v) == 3
            and all(isinstance(a, (int, float)) and not isinstance(a, bool) for a in v))


# ------------------------------------------------------------------------------- selectors
def map_selector(T: Matrix, sel: str) -> tuple[str, list[float] | None]:
    """Mapped selector and, when its axis could not be kept, the mapped direction vector."""
    m = _FACE_SEL.match(sel)
    if not m or m.group("dir") in ("axis", "any"):
        return sel, None
    d = m.group("dir")
    e = [0.0, 0.0, 0.0]
    e[_AXES[d[1]]] = 1.0 if d[0] == "+" else -1.0
    v = apply_vector(T, e)
    n = m.group("n") or ""
    for ax, i in _AXES.items():
        if abs(abs(v[i]) - 1.0) <= AXIS_TOL:
            sign = "+" if v[i] > 0 else "-"
            return f"{m.group('fid')}:{m.group('kind')}:{sign}{ax}:{m.group('ext')}{n}", None
    return f"{m.group('fid')}:{m.group('kind')}:any:any{n}", v


# -------------------------------------------------------------------------------- the plan
def map_plan(plan: dict, T: Sequence[Sequence[float]], taxonomy=None) -> tuple[dict, list[str]]:
    """``plan`` with every frame-bound value mapped by the rigid ``T`` (``x' = T x``).

    Returns ``(mapped_plan, notes)``; ``notes`` lists every selector whose axis could not be kept
    (also written into that feature's ``evidence.note``). The input is not modified.
    For the Track T seed use ``map_plan(gt_plan, invert_rigid(T_scan_to_gt))``.
    """
    if taxonomy is None:
        from recadbench_spec import load_taxonomy

        taxonomy = load_taxonomy()
    M = as_matrix(T)
    probs = rigid_problems(M)
    if probs:
        raise ValueError("not a rigid transform: " + "; ".join(probs))
    out = copy.deepcopy(plan)
    notes: list[str] = []

    for d in out.get("datums") or []:
        d["origin"] = apply_point(M, d["origin"])
        d["direction"] = apply_vector(M, d["direction"])
        if "x_dir" in d:
            d["x_dir"] = apply_vector(M, d["x_dir"])
    for p in out.get("reference_planes") or []:
        p["origin"] = apply_point(M, p["origin"])
        p["normal"] = apply_vector(M, p["normal"])
        p["x_dir"] = apply_vector(M, p["x_dir"])

    for f in out.get("features") or []:
        lost: list[str] = []

        def sel(s: Any, _lost: list[str] = lost) -> Any:
            if not isinstance(s, str):
                return s
            new, vec = map_selector(M, s)
            if vec is not None:
                _lost.append(f"{s} -> {new}: direction {s.split(':')[2]} of the reference "
                             f"frame is [{vec[0]}, {vec[1]}, {vec[2]}] in this frame")
            return new

        params = f.get("params") or {}
        for key, val in list(params.items()):
            try:
                ptype = taxonomy.param_type(f["type"], key)
            except (KeyError, ValueError):
                ptype = None
            if ptype == "vec3" and _is_vec3(val):
                params[key] = apply_point(M, val)
            elif ptype == "vec3_list" and isinstance(val, list):
                params[key] = [apply_point(M, p) if _is_vec3(p) else p for p in val]
            elif ptype == "direction":
                params[key] = apply_vector(M, val) if _is_vec3(val) else sel(val)
            elif ptype in ("selector", "selectors"):
                params[key] = [sel(v) for v in val] if isinstance(val, list) else sel(val)
        if f.get("refs"):
            f["refs"] = [sel(r) for r in f["refs"]]
        if f.get("sketch") and isinstance(f["sketch"].get("plane"), str):
            f["sketch"]["plane"] = sel(f["sketch"]["plane"])
        if lost:
            ev = f.setdefault("evidence", {})
            text = "frame mapping: " + "; ".join(lost)
            ev["note"] = f"{ev['note']} | {text}" if ev.get("note") else text
            notes.extend(f"{f['id']}: {x}" for x in lost)
    return out, notes


def seed_from_gt(gt_plan: dict, T_scan_to_gt: Sequence[Sequence[float]] | None,
                 taxonomy=None) -> tuple[dict, list[str]]:
    """The Track T seed plan: ``gt_plan`` (GT frame) mapped into the scanner frame with
    ``inv(T_scan_to_gt)``; identity when ``T`` is None (synthetic parts)."""
    T = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]] if T_scan_to_gt is None \
        else invert_rigid(T_scan_to_gt)
    mapped, notes = map_plan(gt_plan, T, taxonomy)
    mapped["revision"] = 1
    mapped.pop("change_log", None)
    return mapped, notes


def rotation_about(axis: Sequence[float], deg: float) -> Matrix:
    """Rodrigues rotation (4x4) about a unit ``axis`` through the origin (tests, synthetic T)."""
    n = math.sqrt(sum(a * a for a in axis))
    x, y, z = (a / n for a in axis)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    C = 1 - c
    return [[c + x * x * C, x * y * C - z * s, x * z * C + y * s, 0.0],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s, 0.0],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def compose_rigid(A: Matrix, B: Matrix) -> Matrix:
    """``A @ B`` for 4x4 matrices."""
    return [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
