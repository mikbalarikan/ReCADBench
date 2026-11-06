"""Conformance + contract tests of the build123d adapter (backends/build123d/adapter/adapter.py).

Every call goes through the CLI in a subprocess (the contract the `recad` dispatcher uses).
Skipped when build123d is not importable in the running interpreter.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("build123d")

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent / "adapter" / "adapter.py"
CASES_DIR = HERE.parent.parent / "conformance" / "ir_cases"
CASES = sorted(p.name for p in CASES_DIR.iterdir() if (p / "expected.json").is_file())


def run(*args: str, expect_ok: bool = True) -> dict:
    proc = subprocess.run([sys.executable, str(ADAPTER), *args], capture_output=True, text=True,
                          timeout=300)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line on stdout, got {proc.stdout!r}"
    res = json.loads(lines[0])
    if expect_ok:
        assert proc.returncode == 0 and res["ok"], (res, proc.stderr[-2000:])
    return res


def expected(case: str) -> dict:
    return json.loads((CASES_DIR / case / "expected.json").read_text(encoding="utf-8"))


def close(v: float, ref: float, rel: float) -> bool:
    return abs(v - ref) <= rel * abs(ref)


def validate_features(path: Path) -> None:
    spec = pytest.importorskip("recadbench_spec")
    report = spec.validate_file(str(path), schema="features")
    assert report.ok, [str(e) for e in report.errors]


@pytest.fixture(scope="module")
def replays(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("replays")
    return {c: (run("replay", c, "--out-dir", str(base / c)), base / c) for c in CASES}


def test_cases_present():
    assert len(CASES) == 10


@pytest.mark.parametrize("case", CASES)
def test_replay_matches_expected(case, replays):
    res, out = replays[case]
    exp = expected(case)
    assert res["solids"] == 1
    assert close(res["volume_mm3"], exp["volume_mm3"], exp["tolerance_rel"]), res
    assert res["count"] == exp["feature_count"], res
    assert res["types"] == exp["features"], res
    assert res["unmapped"] == []
    for name in ("native.py", "output.step", "features.json"):
        assert (out / name).is_file()
    doc = json.loads((out / "features.json").read_text(encoding="utf-8"))
    assert doc["tool"] == "build123d" and doc["count"] == exp["feature_count"]
    assert sum(f["counted"] for f in doc["features"]) == doc["count"]
    validate_features(out / "features.json")


@pytest.mark.parametrize("case", CASES)
def test_replay_set_param_round_trip(case, replays, tmp_path):
    _, out = replays[case]
    exp = expected(case)
    sp = exp["set_param"]
    res = run("set-param", str(out / "native.py"), sp["name"], str(sp["value"]), "--out",
              str(tmp_path / "sp.step"), "--native-out", str(tmp_path / "sp.py"))
    assert close(res["volume_before_mm3"], exp["volume_mm3"], exp["tolerance_rel"])
    assert close(res["volume_after_mm3"], sp["volume_mm3"], exp["tolerance_rel"]), res
    assert (tmp_path / "sp.step").is_file()
    edited = (tmp_path / "sp.py").read_text(encoding="utf-8")
    assert f"{sp['name']} = {float(sp['value'])!r}" in edited
    # the edited model is still the same feature tree
    ex = run("extract", str(tmp_path / "sp.py"), "--out", str(tmp_path / "f.json"))
    assert ex["count"] == exp["feature_count"]


@pytest.mark.parametrize("case", CASES)
def test_execute_ir_matches_replay(case, replays, tmp_path):
    exp = expected(case)
    res = run("execute-ir", str(CASES_DIR / case / "re_plan.v1.json"), "--out",
              str(tmp_path / "ir.step"), "--native-out", str(tmp_path / "part.py"))
    rep, _ = replays[case]
    assert res["solids"] == 1
    assert close(res["volume_mm3"], exp["volume_mm3"], exp["tolerance_rel"]), res
    assert close(res["volume_mm3"], rep["volume_mm3"], 1e-6), (res, rep)
    assert res["count"] == exp["feature_count"] and res["unmapped"] == []
    # generated native model is parametric: S5 edit works on it too
    sp = exp["set_param"]
    res2 = run("set-param", str(tmp_path / "part.py"), sp["name"], str(sp["value"]), "--out",
               str(tmp_path / "ir_sp.step"))
    assert close(res2["volume_after_mm3"], sp["volume_mm3"], exp["tolerance_rel"]), res2


def test_execute_ir_is_deterministic(tmp_path):
    ir = CASES_DIR / "C05_rect_pattern" / "re_plan.v1.json"
    run("execute-ir", str(ir), "--out", str(tmp_path / "a.step"), "--native-out",
        str(tmp_path / "a.py"))
    run("execute-ir", str(ir), "--out", str(tmp_path / "b.step"), "--native-out",
        str(tmp_path / "b.py"))
    assert (tmp_path / "a.py").read_bytes() == (tmp_path / "b.py").read_bytes()


def test_execute_ir_rejects_invalid_ir(tmp_path):
    ir_path = CASES_DIR / "C02_through_hole" / "re_plan.v1.json"
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    ir["features"][1]["params"]["diameter"] = {"ref": "undeclared"}
    p = tmp_path / "re_plan.v1.json"
    p.write_text(json.dumps(ir))
    res = run("execute-ir", str(p), "--out", str(tmp_path / "x.step"), expect_ok=False)
    assert not res["ok"] and "undeclared" in res["error"]
    assert not (tmp_path / "x.step").exists()


def _extract(tmp_path: Path, code: str, expect_ok: bool = True) -> dict:
    src = tmp_path / "model.py"
    src.write_text(textwrap.dedent(code))
    out = tmp_path / "features.json"
    run("extract", str(src), "--out", str(out), expect_ok=expect_ok)
    return json.loads(out.read_text(encoding="utf-8"))


def test_ocp_direct_script_is_unmapped(tmp_path):
    doc = _extract(tmp_path, """
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from build123d import Solid
        part = Solid(BRepPrimAPI_MakeBox(10, 10, 10).Shape())
        """)
    assert doc["count"] == 0
    assert any("OCP" in u for u in doc["unmapped"])
    validate_features(tmp_path / "features.json")


def test_topology_level_solid_is_unmapped(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import BuildPart, Box, Solid, add
        with BuildPart() as bp:
            Box(10, 10, 10)
            add(Solid.make_cylinder(2, 20))
        part = bp.part
        """)
    assert any("Solid.make_cylinder" in u for u in doc["unmapped"])


def test_imported_geometry_is_unmapped(tmp_path):
    step = tmp_path / "in.step"
    (tmp_path / "mk.py").write_text("from build123d import Box\npart = Box(5, 5, 5)\n")
    run("export", str(tmp_path / "mk.py"), "--out", str(step))
    doc = _extract(tmp_path, f"""
        from build123d import import_step
        part = import_step({str(step)!r})
        """)
    assert any("import_step" in u for u in doc["unmapped"])


def test_grid_locations_hole_is_hole_plus_pattern(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import BuildPart, Box, GridLocations, Hole, Locations
        t = 5.0
        with BuildPart() as bp:
            Box(40, 30, t)
            with Locations((0, 0, t / 2)):
                with GridLocations(10, 10, 3, 2):
                    Hole(radius=1.5)
        part = bp.part
        """)
    assert [f["taxonomy_type"] for f in doc["features"]] == ["primitive", "hole",
                                                             "pattern_linear"]
    assert doc["count"] == 3 and doc["unmapped"] == []
    assert {"name": "t", "value": 5.0} in doc["parameters"]


def test_loop_counts_once_per_execution(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import BuildPart, Box, Hole, Locations
        with BuildPart() as bp:
            Box(40, 30, 5)
            for x in (-10, 0, 10):
                with Locations((x, 0, 2.5)):
                    Hole(radius=1.5)
        part = bp.part
        """)
    assert [f["taxonomy_type"] for f in doc["features"]] == ["primitive"] + ["hole"] * 3


def test_algebra_booleans(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import Box, Cylinder, Pos
        body = Box(40, 30, 10) - Cylinder(3, 10)       # cut of one feature: part of it
        tool = Cylinder(2, 20) + Pos(5, 0, 0) * Cylinder(2, 20)  # 2-feature tool body
        part = body - tool                               # body - multi-feature body: boolean
        """)
    assert [f["taxonomy_type"] for f in doc["features"]] == ["primitive"] * 4 + ["boolean"]
    assert doc["unmapped"] == []


def test_polar_pattern_and_sketch_ops_not_counted(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import (BuildPart, BuildSketch, Circle, CounterSinkHole, Mode,
                               PolarLocations, extrude, fillet)
        with BuildPart() as bp:
            with BuildSketch():
                Circle(20)
                with PolarLocations(10, 4):
                    Circle(2, mode=Mode.SUBTRACT)
            extrude(amount=5)
            with PolarLocations(15, 3):
                CounterSinkHole(1, 2)
        """)
    assert [f["taxonomy_type"] for f in doc["features"]] == ["extrude", "hole",
                                                             "pattern_circular"]
    assert doc["features"][1]["params"]["kind"] == "countersink"
    assert doc["final_part"] == "last BuildPart" and doc["unmapped"] == []


def test_export_requires_single_solid(tmp_path):
    src = tmp_path / "two.py"
    src.write_text("from build123d import Box, Pos\n"
                   "part = Box(1, 1, 1) + Pos(10, 0, 0) * Box(1, 1, 1)\n")
    res = run("export", str(src), "--out", str(tmp_path / "two.step"), expect_ok=False)
    assert not res["ok"] and res["solids"] == 2
    assert not (tmp_path / "two.step").exists()


def test_set_param_unknown_name_fails(tmp_path, replays):
    _, out = replays["C01_block"]
    res = run("set-param", str(out / "native.py"), "nope", "1", "--out",
              str(tmp_path / "x.step"), expect_ok=False)
    assert not res["ok"] and "nope" in res["error"]


def test_failing_script_writes_gate_failing_features(tmp_path):
    doc = _extract(tmp_path, "raise RuntimeError('boom')\n", expect_ok=False)
    assert doc["count"] == 0 and doc["unmapped"]


# --- review regressions (P3 review round) ------------------------------------------------------

def _types(doc: dict) -> list[str]:
    return [f["taxonomy_type"] for f in doc["features"]]


@pytest.mark.parametrize("body, op", [
    # hand-sewn solid (no parametric op)
    ("""
     w = Wire.make_polygon([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], close=True)
     faces = [Face(w), Face(w).moved(Location((0, 0, 10)))] + [
         Face(Wire.make_polygon([p, q, (q[0], q[1], 10), (p[0], p[1], 10)], close=True))
         for p, q in [((0, 0, 0), (10, 0, 0)), ((10, 0, 0), (10, 10, 0)),
                      ((10, 10, 0), (0, 10, 0)), ((0, 10, 0), (0, 0, 0))]]
     dumb = Solid(Shell(faces))
     """, "chamfer(dumb.edges().group_by(Axis.Z)[-1], length=1)"),
    # unpickled geometry
    ("""
     import pickle
     dumb = pickle.loads(PICKLED)
     """, "fillet(dumb.edges().group_by(Axis.Z)[-1], radius=0.5)"),
    ("""
     import pickle
     dumb = pickle.loads(PICKLED)
     """, "mirror(dumb, about=Plane.XY.offset(-5))"),
])
def test_modifier_or_mirror_on_untraced_body_is_unmapped(tmp_path, body, op):
    import pickle

    import build123d as bd

    pickled = pickle.dumps(bd.Solid.make_box(10, 10, 10))
    code = "from build123d import *\n" + textwrap.dedent(body) + f"part = {op}\n"
    (tmp_path / "model.py").write_text(f"PICKLED = {pickled!r}\n" + code)
    out = tmp_path / "features.json"
    run("extract", str(tmp_path / "model.py"), "--out", str(out))
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert any("not the result of traced" in u for u in doc["unmapped"]), doc


def test_builtins_exec_is_unmapped(tmp_path):
    doc = _extract(tmp_path, """
        import builtins
        from build123d import *
        ns = {}
        builtins.exec("from build123d import Box\\ns = Box(10, 10, 10)", ns)
        part = fillet(ns["s"].edges().group_by(Axis.Z)[-1], radius=1)
        """)
    assert any("builtins" in u for u in doc["unmapped"])


def test_modifier_on_traced_algebra_body_is_mapped(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import *
        a = Box(20, 20, 10)
        part = fillet(a.edges().filter_by(Axis.Z), radius=2)
        part = chamfer(part.edges().group_by(Axis.Z)[-1], length=0.5)
        """)
    assert _types(doc) == ["primitive", "fillet", "chamfer"] and doc["unmapped"] == []


def test_only_features_reaching_final_part_count(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import *
        height = 20.0
        probe = Box(5, 5, 5)
        probe2 = fillet(probe.edges(), radius=1)
        with BuildPart(mode=Mode.PRIVATE) as scratch:
            Cylinder(3, 10)
        with BuildPart() as bp:
            with BuildSketch(Plane.XY):
                Rectangle(40, 30)
            extrude(amount=height)
            Box(5, 5, 50, mode=Mode.PRIVATE)
        part = bp.part
        """)
    assert _types(doc) == ["extrude"] and doc["count"] == 1 and doc["unmapped"] == []
    assert len(doc["not_in_final_part"]) == 4
    validate_features(tmp_path / "features.json")


def test_private_builder_used_via_add_counts(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import *
        with BuildPart(mode=Mode.PRIVATE) as tool:
            Cylinder(3, 30)
        with BuildPart() as bp:
            Box(20, 20, 10)
            add(tool.part, mode=Mode.SUBTRACT)
        part = bp
        """)
    assert doc["count"] == 2 and doc["unmapped"] == [] and "not_in_final_part" not in doc


@pytest.mark.parametrize("final", ["bp.part.solids()[0]", "bp.part.solid()", "bp"])
def test_final_part_sub_solid_or_builder_is_traced(tmp_path, final):
    doc = _extract(tmp_path, f"""
        from build123d import *
        with BuildPart() as bp:
            with BuildSketch(Plane.XY):
                Rectangle(40, 30)
            extrude(amount=20)
        part = {final}
        """)
    assert doc["count"] == 1 and doc["unmapped"] == []


@pytest.mark.parametrize("expr", ["a + a.mirror(Plane.YZ)", "a + mirror(a, about=Plane.YZ)"])
def test_shape_mirror_equals_mirror_op(tmp_path, expr):
    doc = _extract(tmp_path, f"""
        from build123d import *
        a = Box(10, 10, 10, align=(Align.MIN, Align.CENTER, Align.MIN)) \\
            - Pos(5, 0, 10) * Cylinder(2, 30)
        part = {expr}
        """)
    assert _types(doc) == ["primitive", "primitive", "mirror"] and doc["unmapped"] == []


@pytest.mark.parametrize("expr, ptype", [
    ("plate - [Pos(0, 0, 0), Pos(20, 0, 0), Pos(40, 0, 0)] * h", "pattern_linear"),
    ("plate - (h + h.moved(Location((20, 0, 0))) + h.moved(Location((40, 0, 0))))",
     "pattern_linear"),
    ("plate - h - h.moved(Location((20, 0, 0))) - h.moved(Location((40, 0, 0)))",
     "pattern_linear"),
    ("plate - (h + h.rotate(Axis.Z, 90) + h.rotate(Axis.Z, 180))", "pattern_circular"),
])
def test_placement_copies_merged_are_a_pattern(tmp_path, expr, ptype):
    doc = _extract(tmp_path, f"""
        from build123d import *
        plate = Box(60, 60, 5)
        h = Pos(-20, 0, 0) * Cylinder(2, 10)
        part = {expr}
        """)
    assert _types(doc) == ["primitive", "primitive", ptype] and doc["unmapped"] == []
    assert doc["features"][2]["params"]["count"] == 3


def test_reposition_is_not_a_pattern(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import *
        part = Box(10, 10, 10)
        part = part.moved(Location((5, 0, 0)))
        part = part - Cylinder(2, 20)
        """)
    assert _types(doc) == ["primitive", "primitive"]


@pytest.mark.parametrize("argv", [
    ["set-param", "x.py", "height", "abc", "--out", "x.step"],
    ["bogus"],
    ["extract", "x.py"],
    [],
])
def test_usage_errors_are_json_exit_1(argv):
    proc = subprocess.run([sys.executable, str(ADAPTER), *argv], capture_output=True, text=True,
                          timeout=120)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert proc.returncode == 1 and len(lines) == 1
    res = json.loads(lines[0])
    assert res["ok"] is False and "usage" in res["error"]


# review PR #8: untraced geometry smuggled into a traced shape or builder must land in `unmapped`
@pytest.mark.parametrize("code", [
    # kernel handle of a traced Box swapped by user code (no literal `.wrapped`, no OCP import)
    """
    import sys
    from build123d import Box
    mk = sys.modules["OCP.BRepPrimAPI"].BRepPrimAPI_MakeBox(3.0, 4.0, 5.0).Solid()
    part = Box(10, 10, 10)
    setattr(part, "wrap" + "ped", mk)
    part = part.moved(part.location)
    """,
    # same through the instance __dict__ (bypasses the property), then merged
    """
    import sys
    from build123d import Box
    mk = sys.modules["OCP.BRepPrimAPI"].BRepPrimAPI_MakeBox(3.0, 4.0, 5.0).Solid()
    part = Box(10, 10, 10)
    part.__dict__["_wrap" + "ped"] = mk
    part = part + Box(1, 1, 1)
    """,
    # insert() of an untraced solid into a BuildPart
    """
    from build123d import BuildPart, Box, Pos, Solid, Shell, insert
    raw = Pos(5, 0, 0) * Solid(Shell(Box(4, 4, 4).faces()))
    with BuildPart() as bp:
        Box(10, 10, 10)
        insert(raw)
    part = bp.part
    """,
    # untraced op that adds geometry to the builder (bounding_box in ADD mode)
    """
    from build123d import BuildPart, Box, Pos, bounding_box, Mode, Cylinder
    with BuildPart() as bp:
        Box(10, 10, 10)
        bounding_box(Pos(6, 0, 0) * Cylinder(4, 4, mode=Mode.PRIVATE), mode=Mode.ADD)
    part = bp.part
    """,
], ids=["setattr_wrapped", "dict_wrapped", "insert_untraced", "bounding_box_add"])
def test_smuggled_geometry_is_unmapped(tmp_path, code):
    doc = _extract(tmp_path, code)
    assert doc["unmapped"], doc


def test_in_place_build123d_edits_stay_mapped(tmp_path):
    doc = _extract(tmp_path, """
        from build123d import Axis, Box, Cylinder, Location, fillet, insert, BuildPart
        part = Box(10, 10, 10)
        part.move(Location((5, 0, 0)))
        part = part.clean()
        part = part - Cylinder(2, 20)
        part = fillet(part.edges().filter_by(Axis.Z), 1)
        with BuildPart() as bp:
            insert(part)
        part = bp.part
        """)
    assert doc["unmapped"] == [] and doc["count"] == 3, doc



def test_written_files_use_lf(replays, tmp_path):
    # review PR #8: features.json / native outputs were written with CRLF on Windows
    _, out = replays["C01_block"]
    assert b"\r\n" not in (out / "features.json").read_bytes()
    ir = CASES_DIR / "C01_block" / "re_plan.v1.json"
    run("execute-ir", str(ir), "--out", str(tmp_path / "o.step"),
        "--native-out", str(tmp_path / "part.py"))
    assert b"\r\n" not in (tmp_path / "part.py").read_bytes()
