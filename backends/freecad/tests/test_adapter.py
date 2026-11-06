"""Conformance + contract tests for the FreeCAD adapter.

Run with FreeCAD's Python (pytest installed into that env):

    <freecad-env>/bin/python -m pytest backends/freecad/tests -q

Every adapter call runs in its own subprocess, as ``recad freecad …`` does. Per conformance case:
volume within ``tolerance_rel``, ``count == feature_count`` (same taxonomy types, IR order),
``unmapped`` empty, and the ``set-param`` round trip (new volume, saved native re-exports equal).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(p for p in (os.path.join(sys.prefix, "lib"),) if os.path.isdir(p))
pytest.importorskip("FreeCAD", reason="run with FreeCAD's Python (see module docstring)")
ADAPTER = ROOT / "adapter" / "adapter.py"
CASES_DIR = ROOT.parent / "conformance" / "ir_cases"
CASES = sorted(p.name for p in CASES_DIR.iterdir() if (p / "expected.json").is_file())


def run(*args: str, ok: bool = True) -> dict:
    proc = subprocess.run([sys.executable, str(ADAPTER), *args], capture_output=True, text=True,
                          timeout=600)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout must be exactly one JSON line, got: {proc.stdout!r}"
    res = json.loads(lines[0])
    if ok:
        assert proc.returncode == 0 and res["ok"], f"{res}\n--- stderr ---\n{proc.stderr[-3000:]}"
    else:
        assert proc.returncode == 1 and not res["ok"], res
    return res


def expected(case: str) -> dict:
    return json.loads((CASES_DIR / case / "expected.json").read_text())


def within(value: float, target: float, rel: float) -> bool:
    return abs(value - target) <= rel * abs(target)


@pytest.fixture(scope="module")
def replayed(tmp_path_factory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("replay")
    out = {}
    for case in CASES:
        d = base / case
        res = run("replay", case, "--out-dir", str(d))
        out[case] = (d, res)
    return out


@pytest.mark.parametrize("case", CASES)
def test_replay_volume_and_count(case, replayed):
    exp = expected(case)
    d, res = replayed[case]
    for f in ("native.FCStd", "output.step", "features.json"):
        assert (d / f).is_file(), f
    assert res["solids"] == 1
    assert within(res["volume_mm3"], exp["volume_mm3"], exp["tolerance_rel"]), res
    assert res["count"] == exp["feature_count"]
    feats = json.loads((d / "features.json").read_text())
    assert feats["tool"] == "freecad"
    assert feats["unmapped"] == []
    counted = [f["taxonomy_type"] for f in feats["features"] if f["counted"]]
    assert counted == exp["features"], counted
    assert feats["count"] == len(counted)
    names = {p["name"] for p in feats["parameters"]}
    assert exp["set_param"]["name"] in names


@pytest.mark.parametrize("case", CASES)
def test_export_and_extract_standalone(case, replayed, tmp_path):
    exp = expected(case)
    d, _ = replayed[case]
    res = run("export", str(d / "native.FCStd"), "--out", str(tmp_path / "out.step"))
    assert res["solids"] == 1 and res["unit"] == "mm"
    assert within(res["volume_mm3"], exp["volume_mm3"], exp["tolerance_rel"])
    res = run("extract", str(d / "native.FCStd"), "--out", str(tmp_path / "features.json"))
    assert res["count"] == exp["feature_count"] and res["unmapped"] == 0


@pytest.mark.parametrize("case", CASES)
def test_set_param_round_trip(case, replayed, tmp_path):
    exp = expected(case)
    sp = exp["set_param"]
    d, _ = replayed[case]
    native_out = tmp_path / "edited.FCStd"
    res = run("set-param", str(d / "native.FCStd"), sp["name"], str(sp["value"]),
              "--out", str(tmp_path / "sp.step"), "--native-out", str(native_out))
    assert within(res["volume_before_mm3"], exp["volume_mm3"], exp["tolerance_rel"])
    assert within(res["volume_after_mm3"], sp["volume_mm3"], exp["tolerance_rel"]), res
    # the edited native model re-exports to the same volume and still has the same features
    again = run("export", str(native_out), "--out", str(tmp_path / "again.step"))
    assert within(again["volume_mm3"], sp["volume_mm3"], exp["tolerance_rel"])
    feats = run("extract", str(native_out), "--out", str(tmp_path / "f.json"))
    assert feats["count"] == exp["feature_count"]


def test_set_param_unknown_name_fails(replayed, tmp_path):
    d, _ = replayed["C01_block"]
    res = run("set-param", str(d / "native.FCStd"), "no_such_param", "1",
              "--out", str(tmp_path / "x.step"), ok=False)
    assert "not found" in res["error"]


def _make_doc(tmp_path: Path, body: str) -> Path:
    out = tmp_path / "doc.FCStd"
    script = tmp_path / "make.py"
    head = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'replays')!r})\n"
        "from _fc import FreeCAD, Part, new_body, rect, sketch  # noqa: F401\n"
        "doc, body = new_body('neg')\n"
    )
    tail = f"doc.recompute()\ndoc.saveAs({str(out)!r})\n"
    script.write_text(head + textwrap.dedent(body).strip() + "\n" + tail)
    subprocess.run([sys.executable, str(script)], check=True, capture_output=True, timeout=300)
    return out


def test_dumb_solid_is_unmapped(tmp_path):
    native = _make_doc(tmp_path, """
        sk = sketch(body, "XY_Plane")
        rect(sk, 0, 0, 10, 10)
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 5
        box = doc.addObject("Part::Box", "DumbBox")
        feat = doc.addObject("Part::Feature", "Imported")
        feat.Shape = Part.makeSphere(3)
    """)
    out = tmp_path / "f.json"
    res = run("extract", str(native), "--out", str(out))
    feats = json.loads(out.read_text())
    assert res["count"] == 1
    assert res["unmapped"] == 2
    assert any("DumbBox" in u for u in feats["unmapped"])
    assert any("Imported" in u for u in feats["unmapped"])


def test_body_base_feature_is_unmapped(tmp_path):
    native = _make_doc(tmp_path, """
        base = doc.addObject("Part::Feature", "Scan")
        base.Shape = Part.makeBox(10, 10, 10)
        body.BaseFeature = base
    """)
    out = tmp_path / "f.json"
    res = run("extract", str(native), "--out", str(out))
    assert res["count"] == 0 and res["unmapped"] >= 1


def test_export_without_body_fails(tmp_path):
    native = _make_doc(tmp_path, """
        doc.addObject("Part::Box", "OnlyBox")
    """)
    res = run("export", str(native), "--out", str(tmp_path / "x.step"), ok=False)
    assert "exactly one PartDesign::Body" in res["error"]


def test_multitransform_counts_each_transformation(tmp_path):
    native = _make_doc(tmp_path, """
        sk = sketch(body, "XY_Plane")
        rect(sk, 0, 0, 10, 10)
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 5
        doc.recompute()
        mt = body.newObject("PartDesign::MultiTransform", "MultiTransform")
        mt.Originals = [pad]
        lp = body.newObject("PartDesign::LinearPattern", "LP")
        lp.Direction = (body.Origin.OriginFeatures[0], [""])
        lp.Length = 30
        lp.Occurrences = 2
        mi = body.newObject("PartDesign::Mirrored", "MI")
        mi.MirrorPlane = (body.Origin.OriginFeatures[4], [""])
        mt.Transformations = [lp, mi]
        body.Tip = mt
    """)
    out = tmp_path / "f.json"
    res = run("extract", str(native), "--out", str(out))
    assert res["features"] == ["extrude", "pattern_linear", "mirror"], res


_BOOL_DOC = """
    sk = sketch(body, "XY_Plane")
    rect(sk, 0, 0, 20, 20)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 10
    tool = doc.addObject("PartDesign::Body", "ToolBody")
    sk2 = sketch(tool, "XY_Plane")
    rect(sk2, 10, 0, 10, 10)
    p2 = tool.newObject("PartDesign::Pad", "Pad2")
    p2.Profile = sk2
    p2.Length = 20
    {extra}
    doc.recompute()
    bo = body.newObject("PartDesign::Boolean", "Boolean")
    bo.Type = "Cut"
    bo.addObject(tool)
    body.Tip = bo
    tool.Visibility = False
"""


def test_boolean_tool_body_is_not_a_result_body(tmp_path):
    # Regression: the Boolean's tool body used to count as a second result body => export,
    # set-param and replay failed with "expected exactly one PartDesign::Body".
    native = _make_doc(tmp_path, _BOOL_DOC.format(extra=""))
    res = run("export", str(native), "--out", str(tmp_path / "x.step"))
    assert res["solids"] == 1 and within(res["volume_mm3"], 3500.0, 1e-6), res
    out = tmp_path / "f.json"
    res = run("extract", str(native), "--out", str(out))
    # cutting a single-feature tool body is part of that feature (taxonomy note; same as the
    # build123d tracer): Pad + Pad2, boolean folded
    assert res["features"] == ["extrude", "extrude"], res
    feats = json.loads(out.read_text())
    bo = next(f for f in feats["features"] if f["native_type"] == "PartDesign::Boolean")
    assert not bo["counted"] and bo["params"]["merged_into"] == "ToolBody:1"
    assert feats["face_count"] == res_faces(tmp_path, native)


def res_faces(tmp_path: Path, native: Path) -> int:
    return run("export", str(native), "--out", str(tmp_path / "y.step"))["faces"]


def test_boolean_with_multi_feature_tool_is_counted(tmp_path):
    native = _make_doc(tmp_path, _BOOL_DOC.format(extra=textwrap.dedent("""
        doc.recompute()
        fi = tool.newObject("PartDesign::Fillet", "Fillet")
        fi.Base = (p2, ["Edge1"])
        fi.Radius = 1.0
    """).replace("\n", "\n    ")))
    res = run("extract", str(native), "--out", str(tmp_path / "f.json"))
    assert sorted(res["features"]) == ["boolean", "extrude", "extrude", "fillet"], res
    run("export", str(native), "--out", str(tmp_path / "x.step"))


@pytest.mark.parametrize("args", [
    ("set-param", "x.FCStd", "height", "abc", "--out", "x.step"),
    ("export", "x.FCStd"),
    ("no-such-command",),
    (),
])
def test_usage_error_is_one_json_failure(args):
    res = run(*args, ok=False)
    assert res["error"].startswith("usage:"), res


def test_scratch_body_features_are_not_counted(tmp_path):
    # review PR #8: a second body with no solid (Tip cleared) had its features counted although
    # they never reach the exported solid; count must cover the result body only.
    native = _make_doc(tmp_path, """
        sk = sketch(body, "XY_Plane")
        rect(sk, 0, 0, 10, 10)
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 5
        b2 = doc.addObject("PartDesign::Body", "Scratch")
        sk2 = sketch(b2, "XY_Plane", name="Sketch2")
        rect(sk2, 0, 0, 4, 4)
        for i in range(3):
            p = b2.newObject("PartDesign::Pad", f"Pad{i + 2}")
            p.Profile = sk2
            p.Length = 2 + i
        doc.recompute()
        b2.Tip = None
    """)
    out = tmp_path / "f.json"
    res = run("extract", str(native), "--out", str(out))
    feats = json.loads(out.read_text())
    assert res["count"] == 1 and res["unmapped"] == 0, feats
    assert len(feats["not_in_final_part"]) == 3
    step = run("export", str(native), "--out", str(tmp_path / "x.step"))
    assert abs(step["volume_mm3"] - 500.0) < 1e-6


def test_written_files_use_lf(replayed):
    # review PR #8: features.json was written with the platform newline (CRLF on Windows)
    d, _ = replayed["C01_block"]
    assert b"\r\n" not in (d / "features.json").read_bytes()
