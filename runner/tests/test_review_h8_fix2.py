"""H8 hardening round (verifier findings N1-N5 on 85b049c). Each test fails on 85b049c.

Layers (runner README "Security model"):
1. gate state integrity: HMAC under a per-run key in the runner-private key store (N4);
2. CAD code policy: one AST allow-list for build123d code, NX journals and the finish sandbox
   (N2, N3, N5);
3. GT-copy backstop in the evaluator (evaluator/tests/test_gt_copy.py);
4. Glob / Grep patterns checked in full (N1);
5. finish refuses files that appeared or changed outside plan/ cad/ out/ work/ (N2 writers).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os

import pytest
from recadbench_run.agents.dispatcher import Dispatcher
from recadbench_run.compose import ComposeRequest, compose
from recadbench_run.finish import clean_env, extract_isolated, finish
from recadbench_run.gate_hook import COMPOSE_MANIFEST, STATE_FILE, Gate
from recadbench_run.gate_key import KEY_DIR_ENV, RunKey
from runner_helpers import BACKENDS, content_repos, mini_run, plan_doc, seal, write_plan

PART = "from build123d import *\nwith BuildPart() as bp:\n    Box(10, 20, 5)\npart = bp.part\n"
GT_RUNTIME = ('_p = "/".join(["C:", "Users", "x", "rcb", "ReCADBench-g" + "t", "parts", "p",\n'
              '                "g" + "t.st" + "ep"])\n')


def _nx_run():
    spec = importlib.util.spec_from_file_location("nx_run_fix2",
                                                  BACKENDS / "nx" / "runner" / "nx_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _execute(run, code):
    return Gate(run)("mcp__build123d__execute", {"code": code})


def _execute_file(run, src):
    (run / "cad" / "part.py").write_text(src, encoding="utf-8")
    return Gate(run)("mcp__build123d__execute_file", {"path": "cad/part.py"})


# ===================================================================== N1: Glob patterns
@pytest.mark.parametrize("args", [
    {"pattern": "*/../../../../../ReCADBench-gt/parts/*/*"},
    {"pattern": "*/../../../../../ReCADBench-gt/parts/*/*", "path": "."},
    {"pattern": "cad/**/../../../x/*"},
    {"pattern": "{cad,/etc}/*"},
    {"pattern": "*/{.,..}/*"},
    {"pattern": "C:/Users/*/rcb/*"},
    {"pattern": "~/*"},
    {"pattern": "//server/share/*"},
    {"pattern": "*.py", "path": "cad/../.."},
])
def test_n1_glob_pattern_climbing_out_is_denied(tmp_path, args):
    run = mini_run(tmp_path)
    d = Gate(run).check("Glob", args)
    assert not d.allow and d.category == "path", (args, d.reason)


def test_n1_grep_glob_field_is_checked(tmp_path):
    run = mini_run(tmp_path)
    assert not Gate(run).check("Grep", {"pattern": "x", "glob": "../../*"}).allow
    assert Gate(run).check("Grep", {"pattern": "a..b", "glob": "*.py"}).allow  # regex untouched


def test_n1_ordinary_globs_stay_allowed(tmp_path):
    run = mini_run(tmp_path)
    for args in ({"pattern": "cad/**/*.py"}, {"pattern": "**/*.{py,json}"},
                 {"pattern": "*.md", "path": str(run)}, {"pattern": "plan/re_plan.v*.json"}):
        assert Gate(run).check("Glob", args).allow, args


def test_n1_dispatcher_glob_never_lists_outside_the_run(tmp_path):
    """Second line of defence: the dispatcher's own Glob / Grep drop paths outside the run."""
    run = mini_run(tmp_path)
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    disp = Dispatcher(run, start_servers=False)
    res = disp._t_Glob({"pattern": "../*.txt"})
    assert "outside" not in res.text
    assert not disp._t_Grep({"pattern": "secret", "path": str(tmp_path)}).ok


# ===================================================== N2: GT path built at runtime, writers
@pytest.mark.parametrize("line", [
    "part = import_step(_p)",
    "from build123d import import_step as f\npart = f(_p)",
    "import build123d\npart = build123d.import_step(_p)",
    "from build123d.importers import import_step\npart = import_step(_p)",
    "export_step(Box(1, 1, 1), '_c.st' + 'ep')",
    "Box(1, 1, 1).export_stl('x.stl')",
    "from OCP.BRepTools import BRepTools\nBRepTools.Read_s(None, _p, None)",
    "from OCP.STEPControl import STEPControl_Reader\nr = STEPControl_Reader()",
])
def test_n2_runtime_gt_path_and_cad_io_are_denied(tmp_path, line):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    code = "from build123d import *\n" + GT_RUNTIME + line + "\n"
    d = _execute(run, code)
    assert not d.allow and d.category == "code_policy", (line, d.reason)
    d = _execute_file(run, code)
    assert not d.allow, line


def test_n2_finish_refuses_files_written_outside_the_writable_folders(tmp_path, no_gt_env):
    """build123d's writers wrote _c.step into the run root on 85b049c; any file that appears or
    changes outside plan/ cad/ out/ work/ (compose manifest) now refuses the extraction."""
    pytest.importorskip("build123d")
    repos = content_repos(tmp_path)
    run = compose(ComposeRequest(strategy="oracle", skillset="build123d@v1", part_id="syn_part",
                                 out=tmp_path / "runs" / "r", seed_plan=plan_doc(), **repos))
    assert (run / COMPOSE_MANIFEST).is_file()
    assert _execute_file(run, PART).allow
    (run / "_c.step").write_text("ISO-10303-21;", encoding="utf-8")
    res = finish(run)
    assert not res["ok"] and "_c.step (new)" in res["finish"]["error"]
    (run / "_c.step").unlink()
    (run / "lib" / "b3dhelpers" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    res = finish(run)
    assert not res["ok"] and "lib/b3dhelpers/__init__.py (changed)" in res["finish"]["error"]


def test_n2_a_changed_lib_module_is_denied_at_the_gate(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    (run / "lib" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    d = _execute_file(run, PART)
    assert not d.allow and "lib/helper.py" in d.reason  # appeared after compose
    seal(run)  # as if compose had copied it: trusted while unchanged
    assert _execute_file(run, PART).allow


def test_n2_forged_compose_manifest_trusts_nothing(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    (run / "lib" / "helper.py").write_text("import os\n", encoding="utf-8")
    m = json.loads((run / COMPOSE_MANIFEST).read_text(encoding="utf-8"))
    m["files"]["lib/helper.py"] = hashlib.sha256(b"import os\n").hexdigest()
    (run / COMPOSE_MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    assert Gate(run).pins() == {}
    d = _execute_file(run, PART)
    assert not d.allow and "lib/helper.py" in d.reason


# ============================================================ N3: sandbox escape gadgets
N3_PART = ('import operator\nfrom build123d import Box\n'
           '_g = operator.attrgetter("__ini" + "t__.__glo" + "bals__")(Box)\npart = Box(1, 2, 3)\n')
ESCAPES = [
    N3_PART,
    "from build123d import Box\nb = Box.__init__\n",
    "from build123d import Box\nb = getattr(Box, 'x')\n",
    "import functools\nfrom build123d import Box\nf = functools.reduce(getattr, ['a'], Box)\n",
    "import string\ns = string.Formatter()\n",
    "x = (i for i in [1])\nf = x.gi_frame.f_builtins\n",
    "k = 'a' + chr(95) * 2\n",
    "k = b'\\x5f\\x5f'.decode()\n",
    "k = '__glo' + 'bals__'\n",
    "k = f'{1}__x'\n",
    "import sys\n",
    "import io\n",
    "import hashlib\n",
    "import zipimport\n",
    "import importlib\n",
    "import pathlib\n",
    "exec('1')\n",
    "eval('1')\n",
    "compile('1', 'x', 'exec')\n",
    "f = open('x')\n",
    "v = vars()\n",
    "g = globals()\n",
    "from typing import get_type_hints\n",
    "async def f():\n    pass\n",
    "match 1:\n    case _:\n        pass\n",
    "b = __builtins__\n",
    "from . import x\n",
]


@pytest.mark.parametrize("code", ESCAPES)
def test_n3_escape_gadgets_are_denied_by_the_gate(tmp_path, code):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    d = _execute(run, code)
    assert not d.allow and d.category in ("code_policy", "scan_code"), (code, d.reason)


@pytest.mark.parametrize("code", ESCAPES)
def test_n3_escape_gadgets_are_denied_by_the_finish_sandbox(code):
    """b3d_sandbox on its own (the finish-time check, independent of the gate)."""
    import sys

    sys.path.insert(0, str(BACKENDS / "build123d" / "adapter"))
    try:
        import b3d_sandbox
    finally:
        sys.path.pop(0)
    with pytest.raises(b3d_sandbox.SandboxError):
        b3d_sandbox.check_source(code)


def test_n3_sandbox_builtins_and_cwd():
    import sys

    sys.path.insert(0, str(BACKENDS / "build123d" / "adapter"))
    try:
        import b3d_sandbox
    finally:
        sys.path.pop(0)
    bi = b3d_sandbox.restricted_builtins()
    for name in ("open", "getattr", "setattr", "vars", "globals", "locals", "eval", "exec",
                 "compile", "chr", "bytes", "breakpoint"):
        assert name not in bi, name
    with pytest.raises(ImportError):
        bi["__import__"]("os")
    with pytest.raises(ImportError):
        bi["__import__"]("operator")
    assert bi["__import__"]("math").sqrt(4) == 2
    before = os.getcwd()
    with b3d_sandbox.sandbox_cwd() as td:
        assert os.getcwd() == td and os.listdir(td) == []
    assert os.getcwd() == before


def test_n3_finish_sandbox_refuses_on_its_own(tmp_path, no_gt_env):
    """extract_isolated alone (no gate check) still refuses the N3 part: the adapter sandbox."""
    pytest.importorskip("build123d")
    run = mini_run(tmp_path)
    (run / "cad" / "part.py").write_text(N3_PART, encoding="utf-8")
    rep = extract_isolated(run, "build123d", "cad/part.py", 600)
    assert not rep["export"]["ok"] and "sandbox" in rep["export"]["error"]
    assert "operator" in rep["export"]["error"] and rep["_step"] is None


def test_n3_ordinary_build123d_code_stays_allowed(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    code = ("import math\nfrom build123d import *\nfrom b3dhelpers import *\n"
            "step = 360 / 6\nw = 20.0  # IR parameter\n"
            "with BuildPart() as bp:\n    Box(w, 10, 5)\n"
            "    with PolarLocations(4, 6):\n        Hole(1)\n"
            "    fillet(bp.edges().filter_by(Axis.Z), radius=1)\n"
            "part = bp.part\nprint(f'volume {part.volume:.3f}', math.pi)\n"
            "if __name__ == '__main__':\n    show(part)\n")
    assert _execute(run, code).allow
    assert _execute_file(run, code).allow


# ============================================================== N4: gate state integrity
def test_n4_session_code_cannot_touch_files(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    code = ("import io, hashlib\n"
            "with io.open('.' + 'recadbench' + chr(92) + 'gate_state.json', 'wb') as f:\n"
            "    f.write(b'{}')\n")
    d = _execute(run, code)
    assert not d.allow and d.category == "code_policy"


def test_n4_forged_state_with_a_recomputed_digest_is_tampered(tmp_path):
    """85b049c accepted a state whose sha256 was written to the derivable digest file. The
    state is now authenticated by an HMAC under a key the run dir never holds."""
    run = mini_run(tmp_path)
    write_plan(run, 1)
    assert _execute_file(run, PART).allow
    key = RunKey.load(run)
    assert key.doc["state_mac"]
    raw = json.dumps({"executed": {"revision": 1, "fingerprint": "0" * 64, "call": "x",
                                   "t": 0}, "history": []}).encode()
    (run / STATE_FILE).write_bytes(raw)
    # what the 85b049c digest scheme would accept, written where it looked
    old = run.parent / ".recadbench-gate"
    old.mkdir()
    (old / "x.sha256").write_text(hashlib.sha256(raw).hexdigest(), encoding="utf-8")
    st = Gate(run).state()
    assert st.get("tampered") and "changed outside the gate" in st["why"]
    d = _execute_file(run, PART)
    assert not d.allow and d.category == "tampered"
    assert not Gate(run).finish_check()["ok"]


def test_n4_deleted_state_or_missing_key_is_tampered(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    assert _execute_file(run, PART).allow
    (run / STATE_FILE).unlink()
    assert Gate(run).state()["tampered"]
    RunKey.load(run).delete()
    st = Gate(run).state()
    assert st["tampered"] and "no gate key" in st["why"]


def test_n4_the_key_never_enters_the_run_dir_or_agent_processes(tmp_path, no_gt_env):
    repos = content_repos(tmp_path)
    run = compose(ComposeRequest(strategy="oracle", skillset="build123d@v1", part_id="syn_part",
                                 out=tmp_path / "runs" / "r", seed_plan=plan_doc(), **repos))
    key = RunKey.load(run)
    assert key is not None and not str(key.path).startswith(str(run))
    secret = key.doc["key"]
    for p in run.rglob("*"):
        if p.is_file():
            assert secret.encode() not in p.read_bytes(), p
            assert key.path.name.encode() not in p.read_bytes(), p
    mcp = json.loads((run / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert all(s.get("env", {}).get(KEY_DIR_ENV) == "" for s in mcp.values())
    assert KEY_DIR_ENV not in clean_env()
    assert KEY_DIR_ENV in _nx_run().STRIP_ENV
    disp = Dispatcher(run, start_servers=False)
    assert KEY_DIR_ENV in disp.env  # the gate (runner process / hook) needs it ...
    from recadbench_run.gate_key import scrub

    assert KEY_DIR_ENV not in scrub(disp.env)  # ... the CAD servers and nx-run never get it


# ===================================================================== N5: NX journals
J5 = ('import os\nimport sys\nimport zipfile\n'
      '_z = os.path.join("work", "h.dat")\n'
      'with zipfile.ZipFile(_z, "w") as zf:\n'
      '    zf.writestr("helper.py", open(os.path.join("plan", "notes.txt")).read())\n'
      'sys.path.insert(0, _z)\nimport helper  # noqa\n')
NX_ESCAPES = [
    J5,
    "import zipimport\n",
    "import NXOpen\nS = NXOpen.Session.GetSession()\nS.Execute('x', 'y', 'z', [])\n",
    "import NXOpen\nimp = NXOpen.Session.GetSession().DexManager.CreateStep214Importer()\n",
    "import NXOpen\np = NXOpen.Session.GetSession().Parts.Open('gt.prt')\n",
    "import NXOpen.UF\n",
    "import NXOpen.Facet\n",
    "import NXOpen\nNXOpen.Session.GetSession().ListingWindow.WriteLine('x')\n",
]


@pytest.mark.parametrize("journal", NX_ESCAPES)
def test_n5_journal_escapes_are_denied_by_gate_and_nx_run(tmp_path, journal):
    run = mini_run(tmp_path, "nx")
    write_plan(run, 1)
    (run / "plan" / "notes.txt").write_text("import subprocess\n", encoding="utf-8")
    (run / "work").mkdir()
    (run / "cad" / "j5.py").write_text(journal, encoding="utf-8")
    d = Gate(run).check("Bash", {"command": "recadbench nx-run cad/j5.py"})
    assert not d.allow, journal
    why = _nx_run().check_journal(run, (run / "cad" / "j5.py").resolve(), "recadbench nx-run")
    assert why and why.startswith("denied"), journal


def test_n5_nx_helpers_journal_stays_allowed(tmp_path):
    """An nxhelpers journal like the skillset examples (lib/nxhelpers pinned by compose)."""
    run = mini_run(tmp_path, "nx")
    write_plan(run, 1)
    pkg = run / "lib" / "nxhelpers"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("import os  # trusted harness code\n", encoding="utf-8")
    seal(run)
    (run / "cad" / "ex.py").write_text(
        "import math\n\nfrom nxhelpers import nxf, nxh\n\npart = nxh.work_part()\n"
        "d = nxh.Datums(part)\nnxh.expr(part, 'plate_t', 10)\n"
        "sk = nxf.new_sketch(part, d.xy, d.x, d.origin)\n"
        "assert nxf.finish_sketch(sk)['dof'] == 0\nstep = math.pi / 4\n", encoding="utf-8")
    assert Gate(run).check("Bash", {"command": "recadbench nx-run cad/ex.py"}).allow
    assert _nx_run().check_journal(run, (run / "cad" / "ex.py").resolve(), "x") is None
    # the same lib file changed after compose: no longer trusted
    (pkg / "__init__.py").write_text("import os\nos.system('x')\n", encoding="utf-8")
    assert not Gate(run).check("Bash", {"command": "recadbench nx-run cad/ex.py"}).allow
    assert _nx_run().check_journal(run, (run / "cad" / "ex.py").resolve(), "x")


def test_n4_concurrent_commits_stay_consistent(tmp_path):
    """The SDK in-process hook and the project's command hook may commit at the same time: the
    key-store lock keeps state file and MAC in step (no false 'tampered')."""
    import threading

    from recadbench_run.gate_hook import Decision

    run = mini_run(tmp_path)
    errors = []

    def worker(tag):
        try:
            g = Gate(run)
            for i in range(15):
                g.commit(Decision(True, record={"revision": 1, "fingerprint": f"{tag}{i}",
                                                "call": tag, "t": i}))
                assert not g.state().get("tampered")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    st = Gate(run).state()
    assert not st.get("tampered") and len(st["history"]) == 30
