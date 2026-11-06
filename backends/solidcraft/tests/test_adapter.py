"""Conformance + contract tests of the SolidCraft adapter (backends/solidcraft/adapter/adapter.py).

Every call goes through the adapter CLI in a subprocess (the contract the `recad` dispatcher uses),
which itself shells out to the SolidCraft CLI at $RECAD_SOLIDCRAFT_CLI (default `solidcraft-cli`).
Skipped when that CLI is missing. STEP volumes are re-measured independently with build123d/OCP
(exact B-rep volume) when it is importable in the test interpreter.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent / "adapter" / "adapter.py"
REPLAYS = HERE.parent / "replays"
CASES_DIR = HERE.parent.parent / "conformance" / "ir_cases"
CASES = sorted(p.name for p in CASES_DIR.iterdir() if (p / "expected.json").is_file())
CLI = os.environ.get("RECAD_SOLIDCRAFT_CLI") or "solidcraft-cli"

if not shutil.which(CLI):
    pytest.skip(f"SolidCraft CLI not found ({CLI!r}; set RECAD_SOLIDCRAFT_CLI)",
                allow_module_level=True)


def run(*args: str, expect_ok: bool = True) -> dict:
    env = dict(os.environ, RECAD_SOLIDCRAFT_CLI=CLI)
    proc = subprocess.run([sys.executable, str(ADAPTER), *args], capture_output=True, text=True,
                          timeout=600, env=env)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line on stdout, got {proc.stdout!r}"
    res = json.loads(lines[0])
    if expect_ok:
        assert proc.returncode == 0 and res["ok"], (res, proc.stderr[-2000:])
    else:
        assert proc.returncode == (0 if res["ok"] else 1)
    return res


def expected(case: str) -> dict:
    return json.loads((CASES_DIR / case / "expected.json").read_text())


def close(v: float, ref: float, rel: float) -> bool:
    return abs(v - ref) <= rel * abs(ref)


def ocp_measure(step: Path) -> tuple[int, float, int] | None:
    try:
        import build123d as bd
    except ImportError:
        return None
    shape = bd.import_step(str(step))
    return len(shape.solids()), float(shape.volume), len(shape.faces())


def validate_features(path: Path) -> None:
    try:
        import recadbench_spec as spec
    except ImportError:
        return
    report = spec.validate_file(str(path), schema="features")
    assert report.ok, [str(e) for e in report.errors]


def write_script(path: Path, commands: list) -> Path:
    path.write_text(json.dumps({"commands": commands}, indent=1))
    return path


@pytest.fixture(scope="module")
def replays(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("replays")
    return {c: (run("replay", c, "--out-dir", str(base / c), expect_ok=False), base / c)
            for c in CASES}


def test_cases_present():
    assert len(CASES) == 10
    assert sorted(p.stem for p in REPLAYS.glob("C*.json")) == CASES


def test_adapter_is_stdlib_only():
    import ast

    tree = ast.parse(ADAPTER.read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods


@pytest.mark.parametrize("case", CASES)
def test_replay_matches_expected(case, replays):
    res, out = replays[case]
    exp = expected(case)
    assert res["ok"], res
    assert res["solids"] == 1
    assert close(res["volume_mm3"], exp["volume_mm3"], exp["tolerance_rel"]), res
    assert res["count"] == exp["feature_count"], res
    assert res["types"] == exp["features"], res
    assert res["unmapped"] == []
    for name in ("native.json", "output.step", "features.json"):
        assert (out / name).is_file()
    m = ocp_measure(out / "output.step")
    if m is not None:
        solids, vol, _faces = m
        assert solids == 1
        assert close(vol, exp["volume_mm3"], exp["tolerance_rel"]), (vol, exp)
    doc = json.loads((out / "features.json").read_text())
    assert doc["tool"] == "solidcraft" and doc["count"] == exp["feature_count"]
    assert sum(f["counted"] for f in doc["features"]) == doc["count"]
    assert {p["name"] for p in doc["parameters"]} >= {exp["set_param"]["name"]}
    validate_features(out / "features.json")


@pytest.mark.parametrize("case", CASES)
def test_replay_set_param_round_trip(case, replays, tmp_path):
    _, out = replays[case]
    exp = expected(case)
    sp = exp["set_param"]
    res = run("set-param", str(out / "native.json"), sp["name"], str(sp["value"]), "--out",
              str(tmp_path / "sp.step"), "--native-out", str(tmp_path / "sp.json"))
    assert close(res["volume_before_mm3"], exp["volume_mm3"], exp["tolerance_rel"])
    assert close(res["volume_after_mm3"], sp["volume_mm3"], exp["tolerance_rel"]), res
    m = ocp_measure(tmp_path / "sp.step")
    if m is not None:
        assert m[0] == 1 and close(m[1], sp["volume_mm3"], exp["tolerance_rel"]), m
    # the edited native script carries the new value and is still the same feature tree
    edited = json.loads((tmp_path / "sp.json").read_text())
    defs = [c["params"] for c in edited["commands"] if c["command"] == "parameters.add"
            and c["params"]["name"] == sp["name"]]
    assert defs and defs[0]["expression"].startswith(repr(float(sp["value"])))
    ex = run("extract", str(tmp_path / "sp.json"), "--out", str(tmp_path / "f.json"))
    assert ex["count"] == exp["feature_count"]


def _extract(tmp_path: Path, commands: list, expect_ok: bool = True) -> dict:
    src = write_script(tmp_path / "model.json", commands)
    out = tmp_path / "features.json"
    run("extract", str(src), "--out", str(out), expect_ok=expect_ok)
    doc = json.loads(out.read_text())
    validate_features(out)
    return doc


BOX = {"command": "solid.box", "params": {"length": 10, "width": 10, "height": 10}}


def test_imported_step_is_unmapped(tmp_path, replays):
    _, out = replays["C01_block"]
    doc = _extract(tmp_path, [{"command": "file.insert_step",
                               "params": {"path": str(out / "output.step")}}])
    assert doc["count"] == 0
    assert any("file.insert_step" in u for u in doc["unmapped"])
    assert any("BaseFeature" in u for u in doc["unmapped"])


def test_primitive_and_cut_combine_is_boolean(tmp_path):
    doc = _extract(tmp_path, [
        BOX,
        {"command": "solid.box", "params": {"length": 4, "width": 4, "height": 20,
                                            "corner": [3, 3, -5], "operation": "new"}},
        {"command": "solid.combine", "params": {"target": "Body1", "tools": ["Body2"],
                                                "operation": "cut"}},
    ])
    assert [(f["taxonomy_type"], f["counted"]) for f in doc["features"]] == [
        ("primitive", True), ("primitive", True), ("boolean", True)]
    assert doc["count"] == 3 and doc["unmapped"] == []


def test_join_of_single_feature_is_part_of_that_feature(tmp_path):
    doc = _extract(tmp_path, [
        BOX,
        {"command": "sketch.create", "params": {"plane": "XY", "offset": 10}},
        {"command": "sketch.circle.center", "params": {"center": [5, 5], "radius": 3}},
        {"command": "solid.extrude", "params": {"distance": 5, "operation": "new"}},
        {"command": "solid.combine", "params": {"target": "Body1", "tools": ["Body2"],
                                                "operation": "join"}},
    ])
    comb = doc["features"][-1]
    assert comb["taxonomy_type"] == "boolean" and not comb["counted"]
    assert comb["params"]["merged_into"] == "Extrude1"
    assert doc["count"] == 2


def test_suppressed_feature_not_counted(tmp_path):
    doc = _extract(tmp_path, [
        BOX,
        {"command": "solid.fillet", "params": {"edges": [[5, 0, 10]], "radius": 1}},
        {"command": "timeline.suppress", "params": {"feature": "Fillet1", "suppressed": True}},
    ])
    assert doc["count"] == 1 and len(doc["features"]) == 1
    assert doc["excluded"] == [{"native_name": "Fillet1", "native_type": "FilletFeature",
                                "reason": "suppressed"}]


def test_export_requires_single_solid(tmp_path):
    src = write_script(tmp_path / "two.json", [
        BOX, {"command": "solid.box", "params": {"length": 5, "width": 5, "height": 5,
                                                 "corner": [20, 0, 0], "operation": "new"}}])
    res = run("export", str(src), "--out", str(tmp_path / "two.step"), expect_ok=False)
    assert not res["ok"] and res["solids"] == 2
    assert not (tmp_path / "two.step").exists()


def test_export_reports_volume(tmp_path):
    src = write_script(tmp_path / "box.json", [BOX])
    res = run("export", str(src), "--out", str(tmp_path / "box.step"))
    assert res["solids"] == 1 and res["faces"] == 6
    assert close(res["volume_mm3"], 1000.0, 1e-9) and res["needs_measure"] is False


@pytest.mark.parametrize("argv", [["bogus"], ["set-param", "a.json", "p", "not-a-number",
                                              "--out", "x.step"], ["export", "a.json"]])
def test_usage_error_is_one_json_exit_1(argv):
    res = run(*argv, expect_ok=False)
    assert res["ok"] is False and res["error"].startswith("usage:")


def test_added_fillet_counts_one_more(tmp_path):
    """The count is read from the rebuilt timeline: one more fillet command -> count + 1."""
    script = json.loads((REPLAYS / "C10_draft_pocket.json").read_text())
    for c in script["commands"]:
        c.pop("ir", None)  # replay annotations play no part in counting
    base = write_script(tmp_path / "base.json", script["commands"])
    more = write_script(tmp_path / "more.json", [*script["commands"], {
        "command": "solid.fillet", "params": {"edges": [[25, -20, 10], [25, 20, 10]],
                                              "radius": 3}}])
    n0 = run("extract", str(base), "--out", str(tmp_path / "f0.json"))
    n1 = run("extract", str(more), "--out", str(tmp_path / "f1.json"))
    assert n1["count"] == n0["count"] + 1 and n1["types"][-1] == "fillet"


def test_set_param_unknown_name_fails(tmp_path, replays):
    _, out = replays["C01_block"]
    res = run("set-param", str(out / "native.json"), "nope", "1", "--out",
              str(tmp_path / "x.step"), expect_ok=False)
    assert not res["ok"] and "nope" in res["error"]


def test_set_param_on_sketch_dimension_parameter(tmp_path):
    """A parameter defined by a sketch dimension (`name = expr`) is edited by parameters.change."""
    src = write_script(tmp_path / "dim.json", [
        {"command": "sketch.create", "params": {"plane": "XY"}},
        {"command": "sketch.rectangle.two_point", "params": {"p0": [0, 0], "p1": [40, 30]}},
        {"command": "sketch.dimension", "params": {"entities": ["l1"], "type": "length",
                                                   "value": "width = 40"}},
        {"command": "sketch.finish", "params": {}},
        {"command": "solid.extrude", "params": {"distance": 10}},
    ])
    res = run("set-param", str(src), "width", "50", "--out", str(tmp_path / "d.step"),
              "--native-out", str(tmp_path / "d2.json"))
    assert close(res["volume_before_mm3"], 12000.0, 1e-6)
    assert close(res["volume_after_mm3"], 15000.0, 1e-6), res
    last = json.loads((tmp_path / "d2.json").read_text())["commands"][-1]
    assert last["command"] == "parameters.change" and last["params"]["name"] == "width"


def test_failing_script_writes_gate_failing_features(tmp_path):
    doc = _extract(tmp_path, [{"command": "solid.fillet",
                               "params": {"edges": [[0, 0, 0]], "radius": 1}}],
                   expect_ok=False)
    assert doc["count"] == 0 and doc["unmapped"]


def test_saved_design_and_exported_script_are_read(tmp_path):
    """A saved .sc design and its `solidcraft-cli script` export extract like the replay."""
    exp = expected("C09_mirror_bracket")
    sc = tmp_path / "part.sc"
    subprocess.run([shutil.which(CLI), "run", str(REPLAYS / "C09_mirror_bracket.json"), "--save",
                    str(sc), "--quiet"], check=True, capture_output=True, timeout=600)
    script = tmp_path / "part.script.json"
    subprocess.run([shutil.which(CLI), "script", str(sc), "--out", str(script)], check=True,
                   capture_output=True, timeout=600)
    for native in (sc, script):
        out = tmp_path / f"{native.name}.features.json"
        res = run("extract", str(native), "--out", str(out))
        assert res["count"] == exp["feature_count"] and res["unmapped"] == 0, res
        assert res["types"] == exp["features"]


@pytest.mark.parametrize("depth,volume", [(4.0, 37669.3378), (12.0, 33412.2829)])
def test_c10_draft_walls_survive_pocket_depth_change(tmp_path, depth, volume):
    """Draft face picks must stay on the pocket walls for shallow and deep pockets."""
    exp = expected("C10_draft_pocket")
    res = run("set-param", str(REPLAYS / "C10_draft_pocket.json"), "pocket_depth", str(depth),
              "--out", str(tmp_path / "sp.step"))
    assert close(res["volume_after_mm3"], volume, exp["tolerance_rel"]), res


def test_c09_replay_mirrors_features_like_the_ir(tmp_path):
    """C09 mirrors the seed features [F1, F2]; the single-tool join is part of the mirror."""
    cmds = json.loads((REPLAYS / "C09_mirror_bracket.json").read_text())["commands"]
    mirror = [c["params"] for c in cmds if c["command"] == "solid.mirror"]
    assert mirror == [{"features": ["Extrude1", "Hole1"], "plane": "YZ"}]
    doc = _extract(tmp_path, cmds)
    combine = [f for f in doc["features"] if f["native_type"] == "CombineFeature"]
    assert len(combine) == 1 and not combine[0]["counted"]
    assert combine[0]["params"]["merged_into"] == "Mirror1"
    assert doc["count"] == expected("C09_mirror_bracket")["feature_count"]


def test_install_conformance_loop_covers_only_case_dirs():
    text = (HERE.parent / "install.md").read_text()
    assert "ls backends/conformance/ir_cases)" not in text
    assert "ir_cases/C*/" in text
