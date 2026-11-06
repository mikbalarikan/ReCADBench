"""CI tests (no NX) of the NX backend's spec 0.3.0 port: the replays read the 0.3.0 conformance
IRs through replays/_ir.py (circular angle_deg = total sweep, pitch_deg, {"ref"} numbers), and
`recadbench nx-run` enforces backend.yaml denied_tools before NX starts."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
NX = HERE.parent
REPLAYS = NX / "replays"
CASES = NX.parent / "conformance" / "ir_cases"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IR = _load("nx_replay_ir", REPLAYS / "_ir.py")
sys.path.insert(0, str(NX / "runner"))
import nx_run  # noqa: E402

CASE_IDS = sorted(p.name for p in CASES.iterdir() if (p / "re_plan.v1.json").is_file())


# ---------------------------------------------------------------- replays read the 0.3.0 IRs
def test_ten_cases_and_one_replay_each():
    assert len(CASE_IDS) == 10
    for cid in CASE_IDS:
        assert (REPLAYS / f"{cid}.py").is_file(), cid


@pytest.mark.parametrize("cid", CASE_IDS)
def test_replay_reads_its_ir(cid):
    doc = IR.for_journal(str(REPLAYS / f"{cid}.py"))
    assert doc["spec_version"] == "0.3.0" and doc["part_id"] == cid
    src = (REPLAYS / f"{cid}.py").read_text(encoding="utf-8")
    assert "X.ir()" in src and re.search(r"X\.ir_parameters\(\s*part, IR", src), cid
    assert "X.expr(" not in src, f"{cid}: parameters must come from the IR"


def test_ir_loader_rejects_other_spec_versions(tmp_path):
    p = tmp_path / "re_plan.v1.json"
    p.write_text(json.dumps({"spec_version": "0.2.0"}), encoding="utf-8")
    with pytest.raises(IR.IRError):
        IR.load(str(p))


def _c05_with(tmp_path, mutate):
    doc = json.loads((CASES / "C05_rect_pattern" / "re_plan.v1.json").read_text(encoding="utf-8"))
    mutate(doc)
    p = tmp_path / "re_plan.v1.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def _sketch_ref(doc):
    doc["parameters"].append({"name": "plate_w", "value": 60.0})
    doc["features"][0]["sketch"]["entities"][0]["width"] = {"ref": "plate_w"}


def _position_ref(doc):
    doc["features"][1]["params"]["positions"][0][2] = {"ref": "plate_t"}


@pytest.mark.parametrize("mutate", [_sketch_ref, _position_ref], ids=["sketch", "positions"])
def test_ir_loader_rejects_refs_the_replays_do_not_read(tmp_path, mutate):
    # H4 fixer 3 (review): the replays draw sketches / positions as literals; a 0.3.0 sketch
    # {"ref"} there was ignored silently (set-param would not move that number)
    with pytest.raises(IR.IRError, match=r"F[12]: .*(sketch|positions)"):
        IR.load(_c05_with(tmp_path, mutate))


def test_ir_loader_keeps_top_level_param_refs(tmp_path):
    doc = IR.load(_c05_with(tmp_path, lambda d: None))
    assert doc["features"][0]["params"]["distance"] == {"ref": "plate_t"}


DOC = {
    "parameters": [
        {"name": "n", "value": 4},
        {"name": "sweep", "value": 180.0},
        {"name": "pitch", "value": 30.0},
    ]
}


@pytest.mark.parametrize(
    "params,expect",
    [
        ({"count": 6, "angle_deg": 360}, ("span", "360", "6")),
        ({"count": 6}, ("span", "360", "6")),  # 360 is the default
        ({"count": {"ref": "n"}, "angle_deg": 360}, ("span", "360", "n")),
        # total sweep, not span first -> last: 3 x 90 / 3 = pitch 30
        ({"count": 3, "angle_deg": 90}, ("pitch", "(90.0)/(3)", "3")),
        ({"count": 3, "pitch_deg": 90}, ("pitch", "90.0", "3")),
        ({"count": {"ref": "n"}, "angle_deg": {"ref": "sweep"}}, ("pitch", "(sweep)/(n)", "n")),
        ({"count": 3, "pitch_deg": {"ref": "pitch"}}, ("pitch", "pitch", "3")),
    ],
)
def test_circular_spacing_follows_adr_0004(params, expect):
    assert IR.circular_spacing(DOC, params) == expect


@pytest.mark.parametrize(
    "params",
    [
        {"count": 3, "angle_deg": 360, "pitch_deg": 120},  # at most one of the two
        {"count": 3, "pitch_deg": 0},  # zero step
        {"count": 5, "pitch_deg": 90},  # 450 deg > 360: overlap
        {"count": 2.5, "angle_deg": 360},
        {"count": {"ref": "missing"}},
    ],
)
def test_circular_spacing_errors(params):
    with pytest.raises((IR.IRError, KeyError)):
        IR.circular_spacing(DOC, params)


def test_c06_full_circle_from_the_ir():
    doc = IR.for_journal(str(REPLAYS / "C06_circ_pattern.py"))
    assert IR.circular_spacing(doc, IR.feature(doc, "F3")["params"]) == ("span", "360", "6")


# ---------------------------------------------------------------- denied_tools (nx-run)
def test_runner_reads_the_same_denied_tools_as_yaml():
    manifest = yaml.safe_load((NX / "backend.yaml").read_text(encoding="utf-8"))
    assert manifest["interface"]["kind"] == "cli"
    assert nx_run.denied_rules() == manifest["denied_tools"]
    for r in manifest["denied_tools"]:
        assert "recadbench nx-run cad/x.py".startswith(r["tool"])


@pytest.mark.parametrize("path", sorted(REPLAYS.glob("*.py")), ids=lambda p: p.name)
def test_replays_are_not_denied(path):
    assert nx_run.denied("recadbench nx-run cad/x.py", path.read_text(encoding="utf-8")) is None


def _run_cli(run_dir: Path, *args: str) -> tuple[int, dict]:
    # the deny check runs before NX is located or started, so this is CI-safe
    env = dict(os.environ)
    proc = subprocess.run(
        [sys.executable, str(NX / "runner" / "nx_run.py"), *args],
        cwd=str(run_dir),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])


FACET = (
    "import NXOpen, NXOpen.Facet\n"
    "part = NXOpen.Session.GetSession().Parts.Work\n"
    "b = part.FacetedBodies.CreateSTLImportBuilder()\n"
    "b.File = 'input/input.stl'\n"
)


def test_nx_run_refuses_a_scan_import_journal_before_nx(tmp_path):
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "imp.py").write_text(FACET, encoding="utf-8")
    rc, res = _run_cli(tmp_path, "cad/imp.py")
    assert rc == 1 and res["denied"] and res["error"].startswith("denied: cad/imp.py"), res
    last = json.loads((tmp_path / "cad" / "last_run.json").read_text(encoding="utf-8"))
    assert last["denied"] and not last["saved"]
    assert not (tmp_path / "cad" / "part.prt").exists()


def test_nx_run_refuses_a_journal_whose_sibling_module_imports_the_scan(tmp_path):
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "helper.py").write_text(FACET, encoding="utf-8")
    (tmp_path / "cad" / "main.py").write_text("import helper\n", encoding="utf-8")
    rc, res = _run_cli(tmp_path, "cad/main.py")
    assert rc == 1 and res["denied"] and "cad/helper.py" in res["error"], res


SCANHELP = (
    "def bbox(path):\n"
    "    import struct\n"
    "    pts = [l.split()[1:] for l in open(path) if l.strip().startswith('vertex')]\n"
    "    return pts\n"
    "B = bbox('input/input.stl')\n"
)


def test_nx_run_refuses_a_lib_module_that_reads_the_scan(tmp_path):
    # H4 fixer 3 (review): lib/ is on the journal's sys.path; old code scanned cad/ only
    (tmp_path / "cad").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "input.stl").write_text("solid s\nendsolid s\n", encoding="utf-8")
    (tmp_path / "lib" / "scanhelp.py").write_text(SCANHELP, encoding="utf-8")
    (tmp_path / "cad" / "j.py").write_text("import scanhelp\nw = scanhelp.B\n", encoding="utf-8")
    rc, res = _run_cli(tmp_path, "cad/j.py")
    assert rc == 1 and res["denied"] and "lib/scanhelp.py" in res["error"], res
    assert not (tmp_path / "cad" / "part.prt").exists()


@pytest.mark.parametrize(
    "rel",
    ["scanhelp.py", "tools/deep/scanhelp.py", "lib/pkg/__init__.py", "cad/sub/scanhelp.pyw"],
)
def test_any_py_in_the_run_dir_is_checked(tmp_path, rel):
    (tmp_path / "cad").mkdir()
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FACET, encoding="utf-8")
    j = tmp_path / "cad" / "j.py"
    j.write_text("import sys\nsys.path.insert(0, '..')\nimport scanhelp\n", encoding="utf-8")
    hit = nx_run.check_journal(tmp_path.resolve(), j.resolve(), "recadbench nx-run cad/j.py")
    assert hit and rel in hit, hit


def test_nx_lib_outside_the_run_dir_is_checked(tmp_path, monkeypatch):
    run, lib = tmp_path / "run", tmp_path / "elsewhere" / "lib"
    (run / "cad").mkdir(parents=True)
    lib.mkdir(parents=True)
    (lib / "scanhelp.py").write_text(FACET, encoding="utf-8")
    j = run / "cad" / "j.py"
    j.write_text("import scanhelp\n", encoding="utf-8")
    monkeypatch.setenv("RECADBENCH_NX_LIB", str(lib))
    hit = nx_run.check_journal(run.resolve(), j.resolve(), "recadbench nx-run cad/j.py")
    assert hit and "scanhelp.py" in hit, hit


@pytest.mark.parametrize("name", ["lib/scanhelp.pyc", "lib/scan.zip", "cad/fast.pyd"])
def test_opaque_modules_refuse_the_run(tmp_path, name):
    (tmp_path / "cad").mkdir()
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00\x01")
    (tmp_path / "cad" / "j.py").write_text("x = 1\n", encoding="utf-8")
    rc, res = _run_cli(tmp_path, "cad/j.py")
    assert rc == 1 and res["denied"] and name in res["error"], res


def test_pycache_is_purged_before_the_check(tmp_path):
    (tmp_path / "cad").mkdir()
    cache = tmp_path / "lib" / "nxhelpers" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "nxh.cpython-310.pyc").write_bytes(b"\x00")
    nx_run.purge_bytecode(tmp_path.resolve())
    assert not cache.exists()


def test_comments_do_not_refuse_but_a_coding_cookie_cannot_hide_code(tmp_path):
    ok = "# no Convergent bodies, never FitSurface, never input/ (the scan)\nx = 1\n"
    assert nx_run.denied("recadbench nx-run cad/j.py", nx_run.journal_source(ok.encode())) is None
    # utf-7: Python decodes '+AGk-mport trimesh' as 'import trimesh'
    raw = b"# -*- coding: utf-7 -*-\n+AGk-mport trimesh\n"
    assert nx_run.denied("recadbench nx-run cad/j.py", nx_run.journal_source(raw))
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "j.py").write_bytes(raw)
    j = (tmp_path / "cad" / "j.py").resolve()
    assert nx_run.check_journal(tmp_path.resolve(), j, "recadbench nx-run cad/j.py")


def test_ordinary_journals_pass_the_check(tmp_path):
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "ok.py").write_text(
        "from nxhelpers import nxh\npart = nxh.work_part()\n", encoding="utf-8"
    )
    j = (tmp_path / "cad" / "ok.py").resolve()
    assert nx_run.check_journal(tmp_path.resolve(), j, "recadbench nx-run cad/ok.py") is None
