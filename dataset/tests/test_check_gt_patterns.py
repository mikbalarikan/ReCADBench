"""check_gt_patterns.py on a synthetic part with known ground truth (CI)."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("build123d")

from check_gt_patterns import check_part, main  # noqa: E402

PLAN = {
    "spec_version": "0.3.0",
    "part_id": "synthetic_disk",
    "revision": 1,
    "units": "mm",
    "datums": [
        {
            "id": "D1",
            "role": "primary",
            "kind": "plane",
            "origin": [0, 0, 0],
            "direction": [0, 0, 1],
            "x_dir": [1, 0, 0],
        },
        {
            "id": "D2",
            "role": "secondary",
            "kind": "axis",
            "origin": [0, 0, 0],
            "direction": [0, 0, 1],
        },
    ],
    "parameters": [{"name": "n", "value": 3}],
    "features": [
        {
            "id": "F1",
            "type": "extrude",
            "operation": "new",
            "sketch": {
                "plane": "D1",
                "entities": [{"kind": "circle", "center": [0, 0], "radius": 30}],
            },
            "params": {"distance": 10, "direction": [0, 0, 1]},
        },
        {
            "id": "F2",
            "type": "hole",
            "operation": "cut",
            "params": {
                "diameter": 6,
                "through": True,
                "kind": "simple",
                "positions": [[0, 20, 10]],
            },
            "refs": ["F1:plane:+Z:max"],
        },
        {
            "id": "F3",
            "type": "pattern_circular",
            "params": {"features": ["F2"], "axis": "D2", "count": {"ref": "n"}, "pitch_deg": 60},
        },
    ],
}


@pytest.fixture(scope="module")
def step(tmp_path_factory) -> Path:
    """Disk R30 x 10 with three Ø6 holes on r = 20 at 90, 150, 210 deg (seed at +Y)."""
    from build123d import BuildPart, Cylinder, Hole, Locations, PolarLocations, export_step

    with BuildPart() as bp:
        Cylinder(30, 10)
        with Locations((0, 0, 5)):
            with PolarLocations(20, 3, start_angle=90, angular_range=120, endpoint=True):
                Hole(3)
    out = tmp_path_factory.mktemp("gt") / "gt.step"
    export_step(bp.part, str(out))
    return out


def _part(tmp_path: Path, step: Path, plan: dict) -> Path:
    (tmp_path / "gt.step").write_bytes(step.read_bytes())
    (tmp_path / "gt_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return tmp_path


def test_pitch_plan_matches(tmp_path, step) -> None:
    (chk,) = check_part(_part(tmp_path, step, PLAN))
    assert chk.ok and chk.count == 3 and chk.step_deg == 60
    (c,) = chk.candidates
    assert c.status == "match" and sorted(c.found) == [-150.0, 90.0, 150.0]
    assert main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "params",
    [
        {"count": {"ref": "n"}, "angle_deg": 360},  # 0.3.0 full circle: 90 / 210 / 330
        {"count": {"ref": "n"}, "angle_deg": 120},  # total sweep 120: step 40
        {"count": 2, "pitch_deg": 60},  # one instance too few
        {"count": {"ref": "n"}, "pitch_deg": -60},  # wrong sense: 90 / 30 / -30
    ],
)
def test_wrong_pattern_is_reported(tmp_path, step, params) -> None:
    plan = copy.deepcopy(PLAN)
    plan["features"][2]["params"] = {"features": ["F2"], "axis": "D2", **params}
    (chk,) = check_part(_part(tmp_path, step, plan))
    assert not chk.ok and chk.candidates[0].status == "mismatch"
    assert main([str(tmp_path)]) == 1


def test_unobserved_seed_is_not_a_match(tmp_path, step) -> None:
    plan = copy.deepcopy(PLAN)
    plan["features"][1]["params"]["diameter"] = 9  # no Ø9 cylinder in the STEP
    (chk,) = check_part(_part(tmp_path, step, plan))
    assert chk.candidates[0].status == "unobserved" and not chk.ok


# --- cones, tori, mirrored twins, coincident surfaces of other features, unobservable seeds ---


def _plan(part_id: str, features: list[dict], planes: list[dict] | None = None) -> dict:
    plan = {k: copy.deepcopy(PLAN[k]) for k in ("spec_version", "revision", "units", "datums")}
    plan |= {"part_id": part_id, "parameters": [], "features": features}
    if planes:
        plan["reference_planes"] = planes
    return plan


def _plane(pid: str, origin, normal, x_dir) -> dict:
    return {"id": pid, "origin": list(origin), "normal": list(normal), "x_dir": list(x_dir)}


def _base(radius: float, height: float, down: bool = False) -> dict:
    return {
        "id": "BASE",
        "type": "extrude",
        "operation": "new",
        "sketch": {
            "plane": "D1",
            "entities": [{"kind": "circle", "center": [0, 0], "radius": radius}],
        },
        "params": {"distance": height, "direction": [0, 0, -1 if down else 1]},
    }


def _pattern(pid: str, seed: str, count: int, **step) -> dict:
    return {
        "id": pid,
        "type": "pattern_circular",
        "params": {
            "features": [seed],
            "axis": "D2",
            "count": count,
            **(step or {"angle_deg": 360}),
        },
    }


def _export(tmp_path_factory, name: str, part) -> Path:
    from build123d import export_step

    out = tmp_path_factory.mktemp(name) / "gt.step"
    export_step(part, str(out))
    return out


def _rotate_plane(plan: dict, pid: str, deg: float) -> dict:
    """The seed's sketch plane turned by `deg` about +Z: a wrong seed angle in the plan."""
    plan = copy.deepcopy(plan)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    for p in plan["reference_planes"]:
        if p["id"] == pid:
            for k in ("origin", "x_dir", "normal"):
                x, y, z = p[k]
                p[k] = [c * x - s * y, s * x + c * y, z]
    return plan


def _set_pattern(plan: dict, pid: str, **params) -> dict:
    plan = copy.deepcopy(plan)
    f = next(f for f in plan["features"] if f["id"] == pid)
    f["params"] = {k: f["params"][k] for k in ("features", "axis")} | params
    return plan


def _set_param(plan: dict, fid: str, key: str, value) -> dict:
    plan = copy.deepcopy(plan)
    next(f for f in plan["features"] if f["id"] == fid)["params"][key] = value
    return plan


def _check(tmp_path: Path, step: Path, plan: dict, feature: str):
    return next(c for c in check_part(_part(tmp_path, step, plan)) if c.feature == feature)


def _cyl(r: float, h: float):
    from build123d import Align, Cylinder

    return Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))


# Cone: tapered pins (base r 2, taper 5 deg, 5 high) on r = 20 at 90 / 210 / 330 deg.
CONE_PLAN = _plan(
    "synthetic_cone_pins",
    [
        _base(30, 10),
        {
            "id": "PIN",
            "type": "extrude",
            "operation": "add",
            "sketch": {
                "plane": "P_TOP",
                "entities": [{"kind": "circle", "center": [0, 20], "radius": 2}],
            },
            "params": {"distance": 5, "direction": [0, 0, 1], "taper_deg": 5},
        },
        _pattern("PIN_PATTERN", "PIN", 3),
    ],
    [_plane("P_TOP", (0, 0, 10), (0, 0, 1), (1, 0, 0))],
)


@pytest.fixture(scope="module")
def cone_step(tmp_path_factory) -> Path:
    from build123d import (
        Align,
        BuildPart,
        BuildSketch,
        Circle,
        Cylinder,
        Plane,
        PolarLocations,
        extrude,
    )

    with BuildPart() as bp:
        Cylinder(30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with BuildSketch(Plane.XY.offset(10)):
            with PolarLocations(20, 3, start_angle=90):
                Circle(2)
        extrude(amount=5, taper=5)
    return _export(tmp_path_factory, "cone", bp.part)


def test_tapered_seed_matches_cones(tmp_path, cone_step) -> None:
    chk = _check(tmp_path, cone_step, CONE_PLAN, "PIN_PATTERN")
    (c,) = chk.candidates
    assert chk.status == "ok" and c.kind == "cone" and c.status == "match"
    assert sorted(c.found) == [-150.0, -30.0, 90.0]
    assert main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: _set_pattern(p, "PIN_PATTERN", count=3, pitch_deg=100),  # wrong pitch
        lambda p: _rotate_plane(p, "P_TOP", 10),  # wrong seed angle
        lambda p: _set_param(p, "PIN", "taper_deg", -5),  # widening: no such cone
        lambda p: _set_param(p, "PIN", "taper_deg", 3),  # wrong half angle
    ],
)
def test_wrong_cone_pattern_fails(tmp_path, cone_step, mutate) -> None:
    assert _check(tmp_path, cone_step, mutate(CONE_PLAN), "PIN_PATTERN").failed
    assert main([str(tmp_path)]) == 1


# Torus: bayonet tabs = (r, z) bars 8..15 x 1..3 with a full-round outer end (R1), revolved
# 30 deg about the axis from the XZ half-plane (right hand about +Z: sector middle at 15 deg).
TAB_PLAN = _plan(
    "synthetic_bayonet",
    [
        _base(10, 4),
        {
            "id": "TAB",
            "type": "revolve",
            "operation": "add",
            "sketch": {
                "plane": "P_TAB",
                "entities": [
                    {"kind": "line", "p1": [8, 1], "p2": [15, 1]},
                    {
                        "kind": "arc",
                        "center": [15, 2],
                        "radius": 1,
                        "start_deg": -90,
                        "end_deg": 90,
                    },
                    {"kind": "line", "p1": [15, 3], "p2": [8, 3]},
                    {"kind": "line", "p1": [8, 3], "p2": [8, 1]},
                ],
            },
            "params": {"axis": "D2", "angle_deg": 30},
        },
        _pattern("TAB_PATTERN", "TAB", 3),
    ],
    [_plane("P_TAB", (0, 0, 0), (0, -1, 0), (1, 0, 0))],
)


@pytest.fixture(scope="module")
def tab_step(tmp_path_factory) -> Path:
    from build123d import (
        Axis,
        BuildLine,
        BuildPart,
        BuildSketch,
        Line,
        Plane,
        Rot,
        ThreePointArc,
        make_face,
        revolve,
    )

    with BuildPart() as tab:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Line((8, 1), (15, 1))
                ThreePointArc((15, 1), (16, 2), (15, 3))
                Line((15, 3), (8, 3))
                Line((8, 3), (8, 1))
            make_face()
        revolve(axis=Axis.Z, revolution_arc=30)
    part = _cyl(10, 4)
    for k in range(3):
        part = part + Rot(0, 0, 120 * k) * tab.part
    return _export(tmp_path_factory, "tab", part)


def test_revolved_seed_matches_torus_sectors(tmp_path, tab_step) -> None:
    chk = _check(tmp_path, tab_step, TAB_PLAN, "TAB_PATTERN")
    (c,) = chk.candidates
    assert chk.status == "ok" and c.kind == "torus" and c.status == "match"
    assert sorted(c.found) == [-105.0, 15.0, 135.0]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: _set_pattern(p, "TAB_PATTERN", count=3, pitch_deg=100),  # wrong pitch
        lambda p: _rotate_plane(p, "P_TAB", 10),  # wrong seed angle
        lambda p: _set_param(p, "TAB", "angle_deg", 40),  # wrong sector: middle at 20 deg
    ],
)
def test_wrong_torus_pattern_fails(tmp_path, tab_step, mutate) -> None:
    assert _check(tmp_path, tab_step, mutate(TAB_PLAN), "TAB_PATTERN").failed


def test_full_revolve_seed_is_unobservable(tmp_path, tab_step) -> None:
    chk = _check(tmp_path, tab_step, _set_param(TAB_PLAN, "TAB", "angle_deg", 360), "TAB_PATTERN")
    assert chk.status == "unobservable" and not chk.ok and chk.unobservable_seeds == ["TAB"]


# Twins: tangential 8 x 2 slots with R0.5 corners, centre r = 20 at 30 deg, 4 x 90 deg. The
# corner arcs come in mirror pairs at equal rho, so each one also sees its twin's cylinders.
_A30 = math.radians(30)
SLOT_PLAN = _plan(
    "synthetic_slots",
    [
        _base(30, 5),
        {
            "id": "SLOT",
            "type": "extrude",
            "operation": "cut",
            "sketch": {
                "plane": "P_SLOT",
                "entities": [
                    {"kind": "line", "p1": [-3.5, -1], "p2": [3.5, -1]},
                    {
                        "kind": "arc",
                        "center": [3.5, -0.5],
                        "radius": 0.5,
                        "start_deg": -90,
                        "end_deg": 0,
                    },
                    {"kind": "line", "p1": [4, -0.5], "p2": [4, 0.5]},
                    {
                        "kind": "arc",
                        "center": [3.5, 0.5],
                        "radius": 0.5,
                        "start_deg": 0,
                        "end_deg": 90,
                    },
                    {"kind": "line", "p1": [3.5, 1], "p2": [-3.5, 1]},
                    {
                        "kind": "arc",
                        "center": [-3.5, 0.5],
                        "radius": 0.5,
                        "start_deg": 90,
                        "end_deg": 180,
                    },
                    {"kind": "line", "p1": [-4, 0.5], "p2": [-4, -0.5]},
                    {
                        "kind": "arc",
                        "center": [-3.5, -0.5],
                        "radius": 0.5,
                        "start_deg": 180,
                        "end_deg": 270,
                    },
                ],
            },
            "params": {"distance": 5, "direction": [0, 0, -1]},
        },
        _pattern("SLOT_PATTERN", "SLOT", 4),
    ],
    [
        _plane(
            "P_SLOT",
            (20 * math.cos(_A30), 20 * math.sin(_A30), 5),
            (0, 0, 1),
            (-math.sin(_A30), math.cos(_A30), 0),  # tangential
        )
    ],
)


@pytest.fixture(scope="module")
def slot_step(tmp_path_factory) -> Path:
    from build123d import (
        Align,
        BuildPart,
        BuildSketch,
        Cylinder,
        Location,
        Locations,
        Mode,
        Plane,
        RectangleRounded,
        extrude,
    )

    with BuildPart() as bp:
        Cylinder(30, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with BuildSketch(Plane.XY.offset(5)):
            for k in range(4):
                a = math.radians(30 + 90 * k)
                with Locations(
                    Location((20 * math.cos(a), 20 * math.sin(a), 0), (0, 0, 120 + 90 * k))
                ):
                    RectangleRounded(8, 2, 0.5)
        extrude(amount=-5, mode=Mode.SUBTRACT)
    return _export(tmp_path_factory, "slot", bp.part)


def test_mirrored_corner_twins_match_as_union(tmp_path, slot_step) -> None:
    chk = _check(tmp_path, slot_step, SLOT_PLAN, "SLOT_PATTERN")
    assert chk.status == "ok" and len(chk.candidates) == 4
    assert all(c.status == "match" and c.twins == 1 and len(c.found) == 8 for c in chk.candidates)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: _set_pattern(p, "SLOT_PATTERN", count=4, pitch_deg=80),  # wrong pitch
        lambda p: _set_pattern(p, "SLOT_PATTERN", count=3, pitch_deg=90),  # one too few
        lambda p: _rotate_plane(p, "P_SLOT", 5),  # wrong seed angle
    ],
)
def test_wrong_twin_pattern_fails(tmp_path, slot_step, mutate) -> None:
    assert _check(tmp_path, slot_step, mutate(SLOT_PLAN), "SLOT_PATTERN").failed


# Coincident surfaces: four lugs (r 3 at rho 20, z -5..0) at -135 / -45 / 45 / 135 deg and three
# fences of the same outline above them (z 0..4) at -135 / -45 / 45 deg. The lug at 135 deg has
# the fence's cylinder, but outside the fence's extent along the axis.
_LUG = {"kind": "circle", "radius": 3, "center": [-20 * math.sqrt(0.5), -20 * math.sqrt(0.5)]}
FENCE_PLAN = _plan(
    "synthetic_fence",
    [
        _base(20, 5, down=True),
        {
            "id": "LUG",
            "type": "extrude",
            "operation": "add",
            "sketch": {"plane": "D1", "entities": [_LUG]},
            "params": {"distance": 5, "direction": [0, 0, -1]},
        },
        _pattern("LUG_PATTERN", "LUG", 4),
        {
            "id": "FENCE",
            "type": "extrude",
            "operation": "add",
            "sketch": {"plane": "D1", "entities": [_LUG]},
            "params": {"distance": 4, "direction": [0, 0, 1]},
        },
        _pattern("FENCE_PATTERN", "FENCE", 3, pitch_deg=90),
    ],
)


@pytest.fixture(scope="module")
def fence_step(tmp_path_factory) -> Path:
    from build123d import Pos

    part = Pos(0, 0, -5) * _cyl(20, 5)
    for k, deg in enumerate((-135, -45, 45, 135)):
        x, y = 20 * math.cos(math.radians(deg)), 20 * math.sin(math.radians(deg))
        part = part + Pos(x, y, -5) * _cyl(3, 5)
        if k < 3:
            part = part + Pos(x, y, 0) * _cyl(3, 4)
    return _export(tmp_path_factory, "fence", part)


def test_coincident_cylinders_of_other_features_are_ignored(tmp_path, fence_step) -> None:
    checks = {c.feature: c for c in check_part(_part(tmp_path, fence_step, FENCE_PLAN))}
    assert checks["LUG_PATTERN"].ok and checks["FENCE_PATTERN"].ok
    assert sorted(checks["FENCE_PATTERN"].candidates[0].found) == [-135.0, -45.0, 45.0]
    assert main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "params",
    [
        {"count": 4, "pitch_deg": 90},  # a 4th fence where there is only a lug
        {"count": 3, "pitch_deg": -90},  # wrong sense: -135 / 135 / 45
        {"count": 3, "pitch_deg": 80},  # wrong pitch
    ],
)
def test_wrong_fence_pattern_fails(tmp_path, fence_step, params) -> None:
    plan = _set_pattern(FENCE_PLAN, "FENCE_PATTERN", **params)
    assert _check(tmp_path, fence_step, plan, "FENCE_PATTERN").failed


def test_wrong_fence_seed_angle_fails(tmp_path, fence_step) -> None:
    plan = copy.deepcopy(FENCE_PLAN)
    plan["features"][3]["sketch"]["entities"] = [_LUG | {"center": [20 * math.sqrt(0.5)] * 2}]
    assert _check(tmp_path, fence_step, plan, "FENCE_PATTERN").failed  # seed at 45: 45/135/-135


# Unobservable: no off-axis circular edge in the seed -> listed, never OK, never a failure.
def _with_rib(plan: dict) -> dict:
    plan = copy.deepcopy(plan)
    rib = {
        "id": "RIB",
        "type": "extrude",
        "operation": "add",
        "sketch": {
            "plane": "D1",
            "entities": [{"kind": "polygon", "points": [[10, -1], [25, -1], [25, 1], [10, 1]]}],
        },
        "params": {"distance": 12, "direction": [0, 0, 1]},
    }
    plan["features"] += [rib, _pattern("RIB_PATTERN", "RIB", 3)]
    return plan


def test_polygon_seed_is_unobservable_not_ok(tmp_path, step, capsys) -> None:
    checks = {c.feature: c for c in check_part(_part(tmp_path, step, _with_rib(PLAN)))}
    rib = checks["RIB_PATTERN"]
    assert rib.status == "unobservable" and not rib.ok and not rib.failed
    assert rib.candidates == [] and rib.unobservable_seeds == ["RIB"] and checks["F3"].ok
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "RIB_PATTERN: count 3 step 120 deg -> UNOBSERVABLE" in out
    assert "UNOBSERVABLE (not verified, not failed): synthetic_disk RIB_PATTERN" in out


def test_unobservable_does_not_hide_a_failure(tmp_path, step, capsys) -> None:
    plan = _set_pattern(_with_rib(PLAN), "F3", count=3, pitch_deg=50)
    checks = {c.feature: c for c in check_part(_part(tmp_path, step, plan))}
    assert checks["RIB_PATTERN"].status == "unobservable" and checks["F3"].failed
    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "F3: count 3 step 50 deg -> FAIL" in out and "RIB_PATTERN" in out


# Overlapping twins: three kidney slots (60 deg long on rho 20, R1.5 end caps) at 120 deg pitch.
# The end caps at 0 / 60 deg are twins; a plan whose instances of one twin land on the other's
# angles (one long slot) must fail even though the union of angles equals what is found.
def _kidney_plan(count: int, **step) -> dict:
    caps = [
        {
            "kind": "arc",
            "center": [20 * math.cos(math.radians(d)), 20 * math.sin(math.radians(d))],
            "radius": 1.5,
            "start_deg": 0,
            "end_deg": 180,
        }
        for d in (0, 60)
    ]
    walls = [
        {"kind": "arc", "center": [0, 0], "radius": r, "start_deg": 0, "end_deg": 60}
        for r in (21.5, 18.5)
    ]
    seed = {
        "id": "KIDNEY",
        "type": "extrude",
        "operation": "cut",
        "sketch": {"plane": "D1", "entities": caps + walls},
        "params": {"distance": 5, "direction": [0, 0, 1]},
    }
    return _plan("synthetic_kidney", [_base(30, 5), seed, _pattern("KP", "KIDNEY", count, **step)])


def _kidney_part(starts):
    from build123d import (
        Align,
        BuildPart,
        BuildSketch,
        Cylinder,
        Edge,
        Mode,
        Plane,
        SlotArc,
        extrude,
    )

    with BuildPart() as bp:
        Cylinder(30, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with BuildSketch(Plane.XY):
            for s in starts:
                SlotArc(Edge.make_circle(20, Plane.XY, start_angle=s, end_angle=s + 60), 3)
        extrude(amount=5, mode=Mode.SUBTRACT)
    return bp.part


@pytest.fixture(scope="module")
def kidney_step(tmp_path_factory) -> Path:
    return _export(tmp_path_factory, "kidney", _kidney_part([0, 120, 240]))


def test_kidney_slots_match(tmp_path, kidney_step) -> None:
    chk = _check(tmp_path, kidney_step, _kidney_plan(3), "KP")
    assert chk.ok and all(c.twins == 1 and c.overlaps == 0 for c in chk.candidates)


def test_overlapping_twin_instances_fail(tmp_path, kidney_step, capsys) -> None:
    # 5 x 60 deg = one continuous 300 deg slot: c1 at 0..240, c2 at 60..300 share 4 angles
    chk = _check(tmp_path, kidney_step, _kidney_plan(5, pitch_deg=60), "KP")
    assert chk.failed and all(c.status == "mismatch" and c.overlaps for c in chk.candidates)
    assert main([str(tmp_path)]) == 1
    assert "on an occupied angle" in capsys.readouterr().out


def test_overlapping_twins_fail_on_two_slots(tmp_path_factory, tmp_path) -> None:
    # 2 real slots (0..60, 120..180); plan 3 x 60 deg: union {0, 60, 120, 180} = found
    step = _export(tmp_path_factory, "kidney2", _kidney_part([0, 120]))
    assert _check(tmp_path, step, _kidney_plan(3, pitch_deg=60), "KP").failed


# Other sketch-based seed types keep cylinder candidates (not unobservable).
_SWEEP = {
    "id": "F2",
    "type": "sweep",
    "operation": "cut",
    "sketch": {"plane": "D1", "entities": [{"kind": "circle", "center": [0, 20], "radius": 3}]},
    "params": {"path": "F1"},
}


def _sweep_plan() -> dict:
    plan = copy.deepcopy(PLAN)
    plan["features"][1] = copy.deepcopy(_SWEEP)
    return plan


def test_sweep_seed_circles_are_checked(tmp_path, step) -> None:
    (chk,) = check_part(_part(tmp_path, step, _sweep_plan()))
    assert chk.ok and chk.candidates[0].kind == "cylinder"


def test_sweep_seed_wrong_pitch_fails(tmp_path, step) -> None:
    plan = _set_pattern(_sweep_plan(), "F3", count=3, pitch_deg=50)
    (chk,) = check_part(_part(tmp_path, step, plan))
    assert chk.failed
    assert main([str(tmp_path)]) == 1


def test_revolve_seed_not_about_pattern_axis_is_unsupported(tmp_path, tab_step, capsys) -> None:
    plan = _set_param(TAB_PLAN, "TAB", "axis", [0, 0, 1])  # not a datum id
    chk = _check(tmp_path, tab_step, plan, "TAB_PATTERN")
    assert chk.failed and chk.unsupported_seeds == ["TAB"]
    assert main([str(tmp_path)]) == 1
    assert "UNSUPPORTED seed" in capsys.readouterr().out
