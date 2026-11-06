"""Check the circular patterns of a GT plan against its gt.step (GT authoring aid, ADR 0004).

    python dataset/tools/check_gt_patterns.py $RECADBENCH_GT_DIR/<part_id> [...] [--json]

For every `pattern_circular` in `gt_plan.json` the seed features' circular geometry (hole
diameter + positions; circles, arcs and slot ends of a sketch) gives candidate cylinders
(radius, distance from the pattern axis, polar angle). `gt.step` must contain cylinders of that
radius, parallel to the axis, at exactly the instance angles the 0.3.0 IR defines
(`recadbench_spec.circular_offsets`: seed + k * step) -- no more, no fewer. A candidate whose
cylinder does not survive in the STEP (cut away later) is reported as unobserved; each pattern
needs at least one matched candidate.

Runs on the evaluator host only (reads GT; needs build123d / OCP). Prints ASCII; never copies
GT anywhere. Exit 0 = all patterns match, 1 = mismatch, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

R_TOL = 0.02  # mm, cylinder radius
RHO_TOL = 0.05  # mm, distance from the pattern axis
ANG_TOL = 0.2  # deg, instance angle


@dataclass
class Candidate:
    seed: str
    source: str
    radius: float
    rho: float
    theta: float
    expected: list[float] = field(default_factory=list)
    found: list[float] = field(default_factory=list)
    status: str = ""  # match | mismatch | unobserved


@dataclass
class PatternCheck:
    part_id: str
    feature: str
    count: int
    step_deg: float
    candidates: list[Candidate]

    @property
    def ok(self) -> bool:
        st = [c.status for c in self.candidates]
        return "mismatch" not in st and "match" in st


def _frames(plan: dict) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray | None]]:
    """id -> (origin, direction/normal, x_dir or None)."""
    out = {}
    for d in plan["datums"]:
        x = np.array(d["x_dir"], float) if "x_dir" in d else None
        out[d["id"]] = (np.array(d["origin"], float), np.array(d["direction"], float), x)
    for p in plan.get("reference_planes") or []:
        out[p["id"]] = (
            np.array(p["origin"], float),
            np.array(p["normal"], float),
            np.array(p["x_dir"], float),
        )
    return out


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _polar_frame(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ref = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = _unit(ref - a * ref.dot(a))
    return e1, np.cross(a, e1)


def _polar(p: np.ndarray, o: np.ndarray, a: np.ndarray, e1, e2) -> tuple[float, float]:
    w = p - o
    w = w - a * w.dot(a)
    return float(np.linalg.norm(w)), math.degrees(math.atan2(w.dot(e2), w.dot(e1)))


def _seed_circles(f: dict, frames: dict, values: dict) -> list[tuple[str, float, np.ndarray]]:
    """(source, radius, 3D centre) of the circular geometry a seed feature defines."""
    from recadbench_spec.ir import resolve_number

    out = []
    p = f.get("params") or {}
    if f["type"] == "hole" and "diameter" in p:
        r = resolve_number(p["diameter"], values) / 2
        for pos in p.get("positions") or []:
            out.append(("hole", r, np.array(pos, float)))
    sk = f.get("sketch")
    if sk and sk["plane"] in frames and frames[sk["plane"]][2] is not None:
        o, n, x = frames[sk["plane"]]
        n, x = _unit(n), _unit(x)
        y = np.cross(n, x)

        def to3d(uv):
            u, v = (resolve_number(c, values) for c in uv)
            return o + u * x + v * y

        for e in sk["entities"]:
            if e.get("construction"):
                continue
            if e["kind"] in ("circle", "arc"):
                r = resolve_number(e["radius"], values)
                out.append((f"sketch {e['kind']}", r, to3d(e["center"])))
            elif e["kind"] == "slot":
                r = resolve_number(e["radius"], values)
                out.append(("sketch slot c1", r, to3d(e["c1"])))
                out.append(("sketch slot c2", r, to3d(e["c2"])))
    return out


def _step_cylinders(step: Path):
    """[(radius, axis_location, axis_direction)] of all cylindrical faces."""
    from build123d import import_step
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    cyl = []
    for face in import_step(str(step)).faces():
        s = BRepAdaptor_Surface(face.wrapped)
        if s.GetType() != GeomAbs_Cylinder:
            continue
        c = s.Cylinder()
        loc, d = c.Axis().Location(), c.Axis().Direction()
        cyl.append(
            (c.Radius(), np.array([loc.X(), loc.Y(), loc.Z()]), np.array([d.X(), d.Y(), d.Z()]))
        )
    return cyl


def _norm_angle(a: float) -> float:
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if abs(a + 180.0) < 1e-9 else a


def _cluster(angles: list[float]) -> list[float]:
    out: list[float] = []
    for a in sorted(_norm_angle(x) for x in angles):
        if not any(abs(_norm_angle(a - b)) < 0.5 for b in out):
            out.append(a)
    return out


def check_part(part_dir: Path) -> list[PatternCheck]:
    from recadbench_spec import circular_offsets, validate_file
    from recadbench_spec.ir import parameter_values, resolve_number

    plan_path = part_dir / "gt_plan.json"
    report = validate_file(plan_path)
    if not report.ok:
        raise ValueError(f"{plan_path}: invalid: {[str(e) for e in report.errors]}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    values = parameter_values(plan)
    frames = _frames(plan)
    feats = {f["id"]: f for f in plan["features"]}
    patterns = [f for f in plan["features"] if f["type"] == "pattern_circular"]
    if not patterns:
        return []
    cylinders = _step_cylinders(part_dir / "gt.step")
    checks = []
    for pf in patterns:
        p = pf["params"]
        ax = p["axis"]
        if isinstance(ax, str):
            o, a, _ = frames[ax]
        else:
            o, a = np.zeros(3), np.array(ax, float)
        a = _unit(a)
        e1, e2 = _polar_frame(a)
        offsets = circular_offsets(p, values)
        count = int(resolve_number(p["count"], values))
        step = offsets[1] if count > 1 else 0.0
        cands = []
        for sid in p["features"]:
            for source, r, centre in _seed_circles(feats[sid], frames, values):
                rho, theta = _polar(centre, o, a, e1, e2)
                if rho < 0.5:
                    continue  # on the axis: no angle to check
                c = Candidate(sid, source, round(r, 4), round(rho, 4), round(theta, 3))
                c.expected = sorted(_norm_angle(theta + off) for off in offsets)
                hits = []
                for cr, loc, d in cylinders:
                    if abs(abs(d.dot(a)) - 1) > 1e-3 or abs(cr - r) > R_TOL:
                        continue
                    crho, cth = _polar(loc, o, a, e1, e2)
                    if abs(crho - rho) <= RHO_TOL:
                        hits.append(cth)
                c.found = _cluster(hits)
                if not c.found:
                    c.status = "unobserved"
                else:
                    same = len(c.found) == len(c.expected) and all(
                        any(abs(_norm_angle(e - f)) <= ANG_TOL for f in c.found) for e in c.expected
                    )
                    c.status = "match" if same else "mismatch"
                c.expected = [round(x, 2) for x in c.expected]
                c.found = [round(x, 2) for x in c.found]
                cands.append(c)
        checks.append(PatternCheck(plan["part_id"], pf["id"], count, round(step, 6), cands))
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("parts", nargs="+", type=Path, help="GT part folders (gt_plan.json + gt.step)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rc, out = 0, []
    for part in a.parts:
        if not (part / "gt_plan.json").is_file() or not (part / "gt.step").is_file():
            print(f"{part}: needs gt_plan.json and gt.step", file=sys.stderr)
            return 2
        for chk in check_part(part):
            out.append({**asdict(chk), "ok": chk.ok})
            rc |= 0 if chk.ok else 1
            if not a.json:
                print(
                    f"{chk.part_id} {chk.feature}: count {chk.count} step {chk.step_deg:g} deg "
                    f"-> {'OK' if chk.ok else 'FAIL'}"
                )
                for c in chk.candidates:
                    print(
                        f"  {c.seed} {c.source} r={c.radius:g} rho={c.rho:g}: {c.status} "
                        f"expected {c.expected} found {c.found}"
                    )
    if a.json:
        print(json.dumps(out, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
