"""Tests of `recadbench nx-run` (backends/nx/runner) and the NX adapter.

CI part (no NX): usage errors and the missing-NX path. Host part: needs run_journal.exe
(RECADBENCH_NX_RUN_JOURNAL, UGII_BASE_DIR or the default NX 2206 path) and skips cleanly without
it. Known ground truth: analytic volumes and hand-counted feature lists. NX runs are sequential
(the runner holds a host-wide lock).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
NX = HERE.parent
sys.path.insert(0, str(NX / "runner"))
import nx_run  # noqa: E402

try:
    nx_run.find_run_journal()
    HAVE_NX = True
except nx_run.NxEnvError:
    HAVE_NX = False
needs_nx = pytest.mark.skipif(not HAVE_NX, reason="NX run_journal.exe not available on this host")


def nx_run_cli(run_dir: Path, *args: str, env: dict | None = None) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(NX / "runner" / "nx_run.py"), *args],
        cwd=str(run_dir),
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, **(env or {})},
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, proc.stdout + proc.stderr
    return proc.returncode, json.loads(lines[0])


def journal(run_dir: Path, name: str, body: str) -> str:
    (run_dir / "cad").mkdir(parents=True, exist_ok=True)
    (run_dir / "cad" / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return f"cad/{name}"


# Small self-contained modelling snippet for the host tests (an extruded 40x30 block, `height`).
BLOCK = """
import NXOpen, NXOpen.Features
S = NXOpen.Session.GetSession(); part = S.Parts.Work
UPD = NXOpen.SmartObject.UpdateOption.WithinModeling
P = lambda x, y, z: NXOpen.Point3d(float(x), float(y), float(z))
part.Expressions.CreateWithUnits("height=20", part.UnitCollection.FindObject("MilliMeter"))
pts = [(-20, -15), (20, -15), (20, 15), (-20, 15)]
ls = [part.Curves.CreateLine(P(*pts[i], 0), P(*pts[(i + 1) % 4], 0)) for i in range(4)]
sec = part.Sections.CreateSection(0.0095, 0.01, 0.5)
sec.AddToSection([part.ScRuleFactory.CreateRuleCurveDumb(ls)], ls[0], NXOpen.NXObject.Null,
                 NXOpen.NXObject.Null, P(0, 0, 0), NXOpen.Section.Mode.Create, False)
b = part.Features.CreateExtrudeBuilder(NXOpen.Features.Feature.Null)
b.Section = sec
b.Direction = part.Directions.CreateDirection(P(0, 0, 0), NXOpen.Vector3d(0.0, 0.0, 1.0), UPD)
b.Limits.StartExtend.Value.RightHandSide = "0"
b.Limits.EndExtend.Value.RightHandSide = "height"
EXTRUDE = b.CommitFeature(); b.Destroy()
"""


# ---------------------------------------------------------------- CI (no NX)
def test_journal_must_be_under_cad(tmp_path):
    (tmp_path / "x.py").write_text("pass\n")
    rc, res = nx_run_cli(tmp_path, "x.py")
    assert rc == 2 and "under" in res["error"]


def test_missing_journal_and_bad_part(tmp_path):
    (tmp_path / "cad").mkdir()
    rc, res = nx_run_cli(tmp_path, "cad/none.py")
    assert rc == 2 and "no journal" in res["error"]
    j = journal(tmp_path, "a.py", "pass\n")
    rc, res = nx_run_cli(tmp_path, j, "--part", "elsewhere.prt")
    assert rc == 2 and "part must be" in res["error"]


def test_no_nx_gives_one_json_error(tmp_path):
    j = journal(tmp_path, "a.py", "pass\n")
    env = {"RECADBENCH_NX_RUN_JOURNAL": str(tmp_path / "nope.exe"), "UGII_BASE_DIR": str(tmp_path)}
    if HAVE_NX and Path(nx_run.DEFAULT_NX, "NXBIN", "run_journal.exe").is_file():
        pytest.skip("the default NX path exists on this host")
    rc, res = nx_run_cli(tmp_path, j, env=env)
    assert rc == 1 and "run_journal.exe not found" in res["error"]
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert last["ok"] is False


def test_adapter_usage_errors_are_one_json(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(NX / "adapter" / "adapter.py"),
            "extract",
            str(tmp_path / "missing.prt"),
            "--out",
            str(tmp_path / "f.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1 and "no native file" in json.loads(proc.stdout)["error"]
    proc = subprocess.run(
        [sys.executable, str(NX / "adapter" / "adapter.py"), "bogus"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1 and json.loads(proc.stdout)["error"].startswith("usage")


# ---------------------------------------------------------------- host (NX)
@needs_nx
def test_nx_run_creates_saves_and_measures(tmp_path):
    j = journal(tmp_path, "step_01.py", BLOCK)
    rc, res = nx_run_cli(tmp_path, j)
    assert rc == 0, res
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert last["ok"] and last["saved"] and (tmp_path / "cad" / "part.prt").is_file()
    assert last["bodies"] == 1 and abs(last["volume"] - 24000.0) < 1e-6
    assert last["count"] == 1 and last["unmapped"] == []
    assert [p["name"] for p in last["parameters"]] == ["height"]
    assert (tmp_path / "cad" / "logs" / "step_01.log").is_file()

    # second journal opens the saved part and edits the expression: part is re-opened, not new
    j2 = journal(
        tmp_path,
        "step_02.py",
        """
        import NXOpen
        part = NXOpen.Session.GetSession().Parts.Work
        part.Expressions.FindObject("height").RightHandSide = "25"
    """,
    )
    rc, res = nx_run_cli(tmp_path, j2)
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert rc == 0 and last["created"] is False and abs(last["volume"] - 30000.0) < 1e-6

    # a failing journal is a transaction: nothing saved, the part keeps 30000
    j3 = journal(
        tmp_path,
        "step_03.py",
        """
        import NXOpen
        part = NXOpen.Session.GetSession().Parts.Work
        part.Expressions.FindObject("height").RightHandSide = "50"
        raise ValueError("boom")
    """,
    )
    rc, res = nx_run_cli(tmp_path, j3)
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert rc == 1 and not last["ok"] and not last["saved"] and "boom" in last["error"]
    assert "ValueError" in last["traceback"]
    rc, res = nx_run_cli(tmp_path, journal(tmp_path, "step_04.py", "pass\n"))
    assert rc == 0 and abs(res["volume"] - 30000.0) < 1e-6


@needs_nx
def test_timeout_kills_only_own_run(tmp_path):
    j = journal(tmp_path, "slow.py", "import time\ntime.sleep(120)\n")
    rc, res = nx_run_cli(tmp_path, j, "--timeout", "15")
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert rc == 1 and last["timed_out"] and "timeout" in last["error"]
    assert not (tmp_path / "cad" / "part.prt").exists()


@needs_nx
def test_unparameterised_body_is_unmapped(tmp_path):
    """Remove Parameters on a parametric block: the body becomes an unparameterised BREP."""
    j = journal(
        tmp_path,
        "dumb.py",
        BLOCK
        + """
b = part.Features.CreateRemoveParametersBuilder()
b.Objects.Add(EXTRUDE.GetBodies()[0]); b.Commit(); b.Destroy()
""",
    )
    rc, res = nx_run_cli(tmp_path, j)
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert last["bodies"] == 1 and abs(last["volume"] - 24000.0) < 1e-6
    assert last["count"] == 0 and len(last["unmapped"]) == 1
    assert "unparameterized" in last["unmapped"][0]


@needs_nx
def test_imported_step_is_unmapped(tmp_path):
    """A STEP body imported into the part and united into a parametric block stays unmapped."""
    bd = pytest.importorskip("build123d")
    stp = tmp_path / "cube.step"
    bd.export_step(bd.Box(10, 10, 10), str(stp))
    j = journal(
        tmp_path,
        "imp.py",
        BLOCK
        + f"""
before = {{bb.Tag for bb in part.Bodies}}
imp = S.DexManager.CreateStep214Importer()
import os
imp.SettingsFile = os.path.join(os.environ["UGII_BASE_DIR"], "STEP214UG", "step214ug.def")
imp.ImportTo = NXOpen.Step214Importer.ImportToOption.WorkPart
imp.ObjectTypes.Solids = True
imp.InputFile = {str(stp)!r}
imp.OutputFile = {str(tmp_path / "scratch.prt")!r}
imp.FileOpenFlag = False
imp.ProcessHoldFlag = True
imp.Commit(); imp.Destroy()
new = [bb for bb in part.Bodies if bb.Tag not in before]
assert new, "nothing imported"
""",
    )
    env = {}
    if not os.environ.get("UGII_BASE_DIR"):
        env["UGII_BASE_DIR"] = str(nx_run.find_run_journal().parent.parent)
    rc, res = nx_run_cli(tmp_path, j, env=env)
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert last["ok"], last
    assert last["count"] == 1 and last["unmapped"], (
        last
    )  # the extrude counts; the import is unmapped


@needs_nx
def test_adapter_replay_extract_set_param(tmp_path):
    out = tmp_path / "C02"
    proc = subprocess.run(
        [
            sys.executable,
            str(NX / "adapter" / "adapter.py"),
            "replay",
            "C02_through_hole",
            "--out-dir",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 0 and res["ok"] and res["count"] == 2, res
    assert abs(res["volume_mm3"] - 22429.2037) < 1e-3
    feats = json.loads((out / "features.json").read_text())
    assert [f["taxonomy_type"] for f in feats["features"] if f["counted"]] == ["extrude", "hole"]
    before = (out / "native.prt").read_bytes()
    proc = subprocess.run(
        [
            sys.executable,
            str(NX / "adapter" / "adapter.py"),
            "set-param",
            str(out / "native.prt"),
            "hole_d",
            "12",
            "--out",
            str(tmp_path / "sp.step"),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    assert res["ok"] and abs(res["volume_after_mm3"] - 21738.0533) < 1e-3, res
    assert (out / "native.prt").read_bytes() == before  # the input is never written


BOOL3 = """
import _nxr as X

part = X.work()
d = X.Datums(part)


def box(cx, w, h0, h1, op=None, target=None):
    sk = X.new_sketch(part, d.xy, d.x, d.origin)
    ls = X.rect_centered(part, sk, X.point(part, cx, 0, 0), w, 10, u0=cx)
    X.finish(sk)
    return X.extrude(part, X.section(part, ls), (0, 0, 1), h0, h1, op, target)


a = box(0, 20, 0, 10)
b = box(15, 20, 0, 10)
bb = b.GetBodies()[0]
box(15, 4, 10, 15, X.BOOL.Unite, bb)
xx = box(30, 20, 0, 10)
X.unite(part, a.GetBodies()[0], [bb])
X.unite(part, a.GetBodies()[0], [xx.GetBodies()[0]])
"""


@needs_nx
def test_union_with_unrelated_earlier_body_matches_build123d(tmp_path):
    """A (1 extrude) + B (extrude + boss united into B), unrelated X (1 extrude); unite A+B, then
    +X. Both unites have a single-feature operand -> count 4, like the build123d twin
    (a = Box; b = Box + Box; x = Box; a + b + x). Analytic volume 5200 mm3."""
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "_nxr.py").write_bytes((NX / "replays" / "_nxr.py").read_bytes())
    j = journal(tmp_path, "bool3.py", BOOL3)
    rc, res = nx_run_cli(tmp_path, j)
    assert rc == 0 and res["ok"], res
    assert abs(res["volume"] - 5200.0) < 1e-6 and res["bodies"] == 1
    assert res["count"] == 4 and res["unmapped"] == [], res


@needs_nx
@pytest.mark.parametrize("kind", ["Jt", "Psm"])
def test_imported_stl_facet_body_is_unmapped(tmp_path, kind):
    """An STL imported into the part is never a clean model. Psm makes a convergent body (a BREP
    feature row); Jt makes a facet body with no feature row and no solid, which the counter must
    still see (part.FacetedBodies without an associated body)."""
    bd = pytest.importorskip("build123d")
    stl = tmp_path / "cube.stl"
    bd.export_stl(bd.Box(10, 10, 10), str(stl))
    j = journal(
        tmp_path,
        f"imp_{kind}.py",
        BLOCK
        + f"""
import NXOpen.Facet
B = NXOpen.Facet.STLImportBuilder
b2 = part.FacetedBodies.CreateSTLImportBuilder()
b2.File = {stl.as_posix()!r}
b2.STLFileUnits = B.STLFileUnitsTypes.Millimeters
b2.FacetBodyType = B.FacetBodyTypes.{kind}
b2.ShowInformationWindow = False
b2.Commit(); b2.Destroy()
""",
    )
    rc, res = nx_run_cli(tmp_path, j)
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert last["ok"] and last["saved"], last
    assert last["count"] == 1 and last["unmapped"], last
    if kind == "Jt":
        assert last["facet_bodies"] == 1 and last["bodies"] == 1, last
        assert any("imported mesh" in u for u in last["unmapped"]), last


@needs_nx
def test_user_expressions_named_like_nx_names_are_parameters(tmp_path):
    """IR parameter names may look like NX's own (p1_width, Pattern_pitch): they are user
    expressions (IsUserSpecified) and must be listed in parameters[]; NX's p<N> are not."""
    body = BLOCK.replace('"height=20"', '"p1_width=20"').replace('= "height"', '= "p1_width"')
    body += """
part.Expressions.CreateWithUnits("Pattern_pitch=15", part.UnitCollection.FindObject("MilliMeter"))
"""
    j = journal(tmp_path, "names.py", body)
    rc, res = nx_run_cli(tmp_path, j)
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text())
    assert last["ok"] and abs(last["volume"] - 24000.0) < 1e-6, last
    names = {p["name"]: p for p in last["parameters"]}
    assert set(names) == {"p1_width", "Pattern_pitch"}, names
    assert names["p1_width"]["value"] == 20.0 and "Extrude" in names["p1_width"]["feature"]
