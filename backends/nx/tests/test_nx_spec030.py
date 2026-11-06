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


def test_ordinary_journals_pass_the_check(tmp_path):
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "ok.py").write_text(
        "from nxhelpers import nxh\npart = nxh.work_part()\n", encoding="utf-8"
    )
    j = (tmp_path / "cad" / "ok.py").resolve()
    assert nx_run.check_journal(tmp_path.resolve(), j, "recadbench nx-run cad/ok.py") is None
