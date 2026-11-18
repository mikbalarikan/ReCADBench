"""Check the circular patterns of a GT plan against its gt.step (GT authoring aid, ADR 0004).

    python dataset/tools/check_gt_patterns.py $RECADBENCH_GT_DIR/<part_id> [...] [--json]

For every `pattern_circular` in `gt_plan.json` the seed features' circular geometry gives
candidate surfaces located about the pattern axis (distance rho, polar angle):

* hole diameter + positions, and circles / arcs / slot ends of an extrude sketch -> cylinders;
  with `taper_deg` -> cones, matched on the radius at the seed sketch plane and the half angle
  (sense included);
* circles / arcs of a revolve about the pattern axis itself (angle < 360) -> torus sectors,
  matched on major / minor radius, the angle being the middle of the revolved sector;
* circles / arcs / slot ends of any other sketch-based seed (sweep, loft, ...) -> cylinders
  (radius, rho, angle; no extent along the axis).

`gt.step` must contain such surfaces, parallel to the axis, at exactly the instance angles the
0.3.0 IR defines (`recadbench_spec.circular_offsets`: seed + k * step) -- no more, no fewer.
Seed circles that share (kind, radius, rho) -- e.g. mirrored slot corners -- find each other's
surfaces, so the union of their expected angles is compared with what is found. If two of those
expected angles coincide (one twin's instance lands on another's, or the pattern wraps onto
itself), the plan implies overlapping instances: mismatch, whatever the STEP holds. Surfaces
outside the seed extrude's extent along the axis (another feature's coincident cylinder) are
ignored.

A candidate whose surface does not survive in the STEP (cut away later) is reported as
unobserved; each pattern needs at least one matched candidate. A pattern whose seeds have no
locatable off-axis circular edge (polygon profile, arcs centred on the axis, full revolve) is
reported UNOBSERVABLE: listed, not verified, not a failure. A revolve seed with off-axis circles
that is not revolved about the pattern axis (or names its axis other than by datum id) cannot be
checked by this tool: reported as an UNSUPPORTED seed, which fails the pattern.

Runs on the evaluator host only (reads GT; needs build123d / OCP). Prints ASCII; never copies
GT anywhere. Exit 0 = every pattern matches or is unobservable, 1 = mismatch, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

R_TOL = 0.02  # mm, cylinder / cone radius at the seed sketch plane, torus minor radius
RHO_TOL = 0.05  # mm, distance from the pattern axis (torus: major radius)
ANG_TOL = 0.2  # deg, instance angle
TAPER_TOL = 0.05  # deg, cone half angle vs the seed's taper_deg
Z_TOL = 0.02  # mm, minimum overlap of a surface with the seed's extent along the pattern axis
ON_AXIS = 0.5  # mm, a centre this close to the pattern axis has no angle to check


@dataclass
class Candidate:
    seed: str
    source: str
    kind: str  # cylinder | cone | torus
    radius: float  # cylinder / cone: radius at the seed sketch plane; torus: minor radius
    rho: float  # distance from the pattern axis (torus: major radius)
    theta: float  # seed angle (torus: middle of the revolved sector)
    taper_deg: float | None = None
    extent: list[float] | None = None  # seed extrude's span along the pattern axis
    twins: int = 0  # other seed circles of the same (kind, radius, rho): angles compared as union
    overlaps: int = 0  # expected instance angles the plan puts twice on this surface (-> mismatch)
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
    unobservable_seeds: list[str] = field(default_factory=list)
    unsupported_seeds: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """ok | fail | unobservable (no seed has a locatable off-axis circular edge)."""
        if self.unsupported_seeds:
            return "fail"
        if not self.candidates:
            return "unobservable"
        st = [c.status for c in self.candidates]
        return "ok" if "mismatch" not in st and "match" in st else "fail"

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass
class _Probe:
    cand: Candidate
    anchor: np.ndarray  # circle centre on the seed sketch plane (cone radius reference)
    slope: float = 0.0  # cones: expected dr per unit length along +axis


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


def _norm_angle(a: float) -> float:
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if abs(a + 180.0) < 1e-9 else a


def _cluster(angles: list[float]) -> list[float]:
    out: list[float] = []
    for a in sorted(_norm_angle(x) for x in angles):
        if not any(abs(_norm_angle(a - b)) < 0.5 for b in out):
            out.append(a)
    return out


def _sketch_circles(f: dict, frames: dict, values: dict) -> list[tuple[str, float, np.ndarray]]:
    """(source, radius, 3D centre) of the circles / arcs / slot ends of a seed's sketch."""
    from recadbench_spec.ir import resolve_number

    sk = f.get("sketch")
    if not (sk and sk["plane"] in frames and frames[sk["plane"]][2] is not None):
        return []
    o, n, x = frames[sk["plane"]]
    n, x = _unit(n), _unit(x)
    y = np.cross(n, x)

    def to3d(uv):
        u, v = (resolve_number(c, values) for c in uv)
        return o + u * x + v * y

    out = []
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


def _extrude_axial(f: dict, frames: dict, values: dict, o, a) -> tuple[float, list[float] | None]:
    """(sense of the extrude along +a, [lo, hi] span along a or None if not axial / unknown)."""
    from recadbench_spec.ir import resolve_number

    p = f.get("params") or {}
    po, n, _ = frames[f["sketch"]["plane"]]
    d = _unit(np.array(p.get("direction") or n, float))
    da = float(d.dot(a))
    sense = 1.0 if da >= 0 else -1.0
    if abs(abs(da) - 1) > 1e-3 or "distance" not in p:
        return sense, None
    h0 = float((po - o).dot(a))
    dist = resolve_number(p["distance"], values)
    if p.get("symmetric"):
        ends = [h0 - dist * da / 2, h0 + dist * da / 2]
    else:
        ends = [h0, h0 + dist * da]
        if "distance2" in p:
            ends.append(h0 - resolve_number(p["distance2"], values) * da)
    return sense, [min(ends), max(ends)]


class Unsupported(Exception):
    """A seed with off-axis circular geometry whose surfaces this tool cannot locate."""


def _seed_probes(f: dict, frames: dict, values: dict, o, a, e1, e2) -> list[_Probe]:
    """The surfaces a seed must leave in the STEP, located about the pattern axis.

    [] = nothing locatable (unobservable). Raises Unsupported for off-axis circular geometry
    the tool cannot turn into a surface.
    """
    from recadbench_spec.ir import resolve_number

    sid, p = f["id"], f.get("params") or {}
    out: list[_Probe] = []
    if f["type"] == "hole" and "diameter" in p:
        r = resolve_number(p["diameter"], values) / 2
        for pos in p.get("positions") or []:
            c = np.array(pos, float)
            rho, th = _polar(c, o, a, e1, e2)
            if rho >= ON_AXIS:
                out.append(_Probe(Candidate(sid, "hole", "cylinder", r, rho, th), c))
    circles = _sketch_circles(f, frames, values)
    if f["type"] == "extrude" and circles:
        taper = resolve_number(p["taper_deg"], values) if p.get("taper_deg") else None
        sense, extent = _extrude_axial(f, frames, values, o, a)
        kind = "cone" if taper else "cylinder"
        slope = -math.tan(math.radians(taper)) * sense if taper else 0.0
        for source, r, c in circles:
            rho, th = _polar(c, o, a, e1, e2)
            if rho >= ON_AXIS:
                cand = Candidate(sid, source, kind, r, rho, th, taper, extent)
                out.append(_Probe(cand, c, slope))
    elif f["type"] == "revolve":
        # revolved about the pattern axis itself: each circle / arc becomes a torus sector whose
        # middle is the instance angle (right hand about the revolve axis); a full revolve has none
        if not any(_polar(c, o, a, e1, e2)[0] >= ON_AXIS for _, _, c in circles):
            return out
        if not (isinstance(p.get("axis"), str) and p["axis"] in frames):
            raise Unsupported(f"{sid}: revolve axis {p.get('axis')!r} is not a datum id")
        ro, rd, _ = frames[p["axis"]]
        rd = _unit(rd)
        sweep = resolve_number(p.get("angle_deg", 360), values)
        w = ro - o
        coaxial = abs(abs(rd.dot(a)) - 1) < 1e-6 and np.linalg.norm(w - a * w.dot(a)) < 1e-3
        if not coaxial:
            raise Unsupported(f"{sid}: revolved about an axis other than the pattern axis")
        if abs(sweep) < 360 - 1e-6:
            sign = 1.0 if rd.dot(a) > 0 else -1.0
            for source, r, c in circles:
                rho, th = _polar(c, o, a, e1, e2)
                if rho >= ON_AXIS:
                    mid = _norm_angle(th + sign * sweep / 2)
                    out.append(_Probe(Candidate(sid, source, "torus", r, rho, mid), c))
    elif f["type"] != "hole":
        # any other sketch-based seed (sweep, loft, ...): cylinders of its sketch circles as
        # located on the sketch plane; no extent along the axis is known
        for source, r, c in circles:
            rho, th = _polar(c, o, a, e1, e2)
            if rho >= ON_AXIS:
                out.append(_Probe(Candidate(sid, source, "cylinder", r, rho, th), c))
    return out


def _step_surfaces(step: Path) -> list[dict]:
    """Cylindrical, conical and toroidal faces: axis, radii, sample points, sector middle."""
    from build123d import import_step
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Torus

    def vec(v):
        return np.array([v.X(), v.Y(), v.Z()])

    kinds = {GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone", GeomAbs_Torus: "torus"}
    out = []
    for face in import_step(str(step)).faces():
        s = BRepAdaptor_Surface(face.wrapped)
        kind = kinds.get(s.GetType())
        if kind is None:
            continue
        g = {"cylinder": s.Cylinder, "cone": s.Cone, "torus": s.Torus}[kind]()
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face.wrapped)
        um, vm = (u0 + u1) / 2, (v0 + v1) / 2
        surf = {
            "kind": kind,
            "loc": vec(g.Axis().Location()),
            "dir": vec(g.Axis().Direction()),
            "pts": np.array([vec(s.Value(u, v)) for u in (u0, um, u1) for v in (v0, vm, v1)]),
            "mid": vec(s.Value(um, vm)),
            "u_span": math.degrees(u1 - u0),
        }
        if kind == "cylinder":
            surf["radius"] = g.Radius()
        elif kind == "cone":
            surf["radius"], surf["semi"] = g.RefRadius(), g.SemiAngle()
        else:
            surf["radius"], surf["minor"] = g.MajorRadius(), g.MinorRadius()
        out.append(surf)
    return out


def _hit(pr: _Probe, s: dict, o, a, e1, e2) -> float | None:
    """Polar angle of surface `s` if it is the surface probe `pr` describes, else None."""
    c = pr.cand
    if s["kind"] != c.kind or abs(abs(s["dir"].dot(a)) - 1) > 1e-3:
        return None
    if c.extent is not None:
        h = (s["pts"] - o) @ a
        if min(h.max(), c.extent[1]) - max(h.min(), c.extent[0]) <= Z_TOL:
            return None  # another feature's surface, outside the seed's span
    if c.kind == "torus":
        lrho, _ = _polar(s["loc"], o, a, e1, e2)
        if (
            lrho > RHO_TOL
            or abs(s["radius"] - c.rho) > RHO_TOL
            or abs(s["minor"] - c.radius) > R_TOL
            or s["u_span"] >= 360 - 1e-6
        ):
            return None
        return _polar(s["mid"], o, a, e1, e2)[1]
    rho, th = _polar(s["loc"], o, a, e1, e2)
    if abs(rho - c.rho) > RHO_TOL:
        return None
    if c.kind == "cylinder":
        return th if abs(s["radius"] - c.radius) <= R_TOL else None
    # cone: radius where it crosses the seed sketch plane, and its sense along the axis
    d, t = s["dir"], math.tan(s["semi"])
    if abs(s["radius"] + float((pr.anchor - s["loc"]).dot(d)) * t - c.radius) > R_TOL:
        return None
    slope = t * float(d.dot(a))  # dr per unit length along +a
    if abs(math.degrees(math.atan(slope) - math.atan(pr.slope))) > TAPER_TOL:
        return None
    return th


def _same_surface(p: Candidate, q: Candidate) -> bool:
    return (
        p.kind == q.kind
        and abs(p.radius - q.radius) <= R_TOL
        and abs(p.rho - q.rho) <= RHO_TOL
        and p.taper_deg == q.taper_deg
        and p.extent == q.extent
    )


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
    surfaces = _step_surfaces(part_dir / "gt.step")
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
        probes: list[_Probe] = []
        unobservable, unsupported = [], []
        for sid in p["features"]:
            try:
                got = _seed_probes(feats[sid], frames, values, o, a, e1, e2)
            except Unsupported:
                unsupported.append(sid)
                continue
            probes += got
            if not got:
                unobservable.append(sid)
        # seed circles of the same surface (kind, radius, rho, taper, extent) find each other's
        # instances, so the union of their expected angles is what must be found
        groups: list[list[_Probe]] = []
        for pr in probes:
            g = next((g for g in groups if _same_surface(g[0].cand, pr.cand)), None)
            if g is None:
                groups.append([pr])
            else:
                g.append(pr)
        for g in groups:
            # seed arcs at one angle (an end round split in two arcs) are one surface, not twins
            seeds: list[float] = []
            for q in g:
                if not any(abs(_norm_angle(q.cand.theta - t)) <= ANG_TOL for t in seeds):
                    seeds.append(q.cand.theta)
            raw = [_norm_angle(t + off) for t in seeds for off in offsets]
            # two instances (of one seed circle or of twins) on one angle: the plan implies
            # overlapping instances (e.g. too long a pattern of kidney slots), never a match
            overlaps = sum(
                any(abs(_norm_angle(x - y)) <= ANG_TOL for y in raw[:i]) for i, x in enumerate(raw)
            )
            expected = _cluster(raw)
            found = _cluster(
                [h for s in surfaces if (h := _hit(g[0], s, o, a, e1, e2)) is not None]
            )
            if not found:
                status = "unobserved"
            else:
                same = len(found) == len(expected) and all(
                    any(abs(_norm_angle(e - f)) <= ANG_TOL for f in found) for e in expected
                )
                status = "match" if same and not overlaps else "mismatch"
            for q in g:
                c = q.cand
                c.status, c.twins, c.overlaps = status, len(g) - 1, overlaps
                c.expected = [round(x, 2) for x in expected]
                c.found = [round(x, 2) for x in found]
                c.radius, c.rho, c.theta = round(c.radius, 4), round(c.rho, 4), round(c.theta, 3)
                if c.extent is not None:
                    c.extent = [round(x, 4) for x in c.extent]
        checks.append(
            PatternCheck(
                plan["part_id"],
                pf["id"],
                count,
                round(step, 6),
                [q.cand for q in probes],
                unobservable,
                unsupported,
            )
        )
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("parts", nargs="+", type=Path, help="GT part folders (gt_plan.json + gt.step)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rc, out, unobservable = 0, [], []
    for part in a.parts:
        if not (part / "gt_plan.json").is_file() or not (part / "gt.step").is_file():
            print(f"{part}: needs gt_plan.json and gt.step", file=sys.stderr)
            return 2
        for chk in check_part(part):
            out.append({**asdict(chk), "status": chk.status, "ok": chk.ok})
            rc |= 1 if chk.failed else 0
            if chk.status == "unobservable":
                unobservable.append(f"{chk.part_id} {chk.feature}")
            if not a.json:
                print(
                    f"{chk.part_id} {chk.feature}: count {chk.count} step {chk.step_deg:g} deg "
                    f"-> {chk.status.upper()}"
                )
                for c in chk.candidates:
                    twins = f" (+{c.twins} twin)" if c.twins else ""
                    if c.overlaps:
                        twins += f" [plan puts {c.overlaps} instance(s) on an occupied angle]"
                    taper = f" taper={c.taper_deg:g}" if c.taper_deg else ""
                    print(
                        f"  {c.seed} {c.source} {c.kind} r={c.radius:g}{taper} rho={c.rho:g}"
                        f"{twins}: {c.status} expected {c.expected} found {c.found}"
                    )
                if chk.unsupported_seeds:
                    print(
                        f"  UNSUPPORTED seed (off-axis circles, surfaces not locatable): "
                        f"{', '.join(chk.unsupported_seeds)}"
                    )
                if chk.unobservable_seeds:
                    print(
                        f"  not verifiable (no off-axis circular edge): "
                        f"{', '.join(chk.unobservable_seeds)}"
                    )
    if a.json:
        print(json.dumps(out, indent=2))
    elif unobservable:
        print(f"UNOBSERVABLE (not verified, not failed): {'; '.join(unobservable)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
