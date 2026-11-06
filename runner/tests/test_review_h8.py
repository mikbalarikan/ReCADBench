"""Regression tests for the H8/P10 review findings (each failed on 5b26f8b).

Blocker: finish executed agent code the gate never vetted, unsandboxed, with the worker's full
environment. Majors: revision-rule bypass through other model-mutating tools, MCP tools writing
protected files, long path arguments skipping the path check, GT paths inside code, a planted
``recad_cli`` package shadowing the adapter, replay runs labelled as a model. Minors: NX journal
escapes, skillset label not tied to its tag, harness default, expand validation, efficiency in the
two-step flow, unknown replay script, system prompt size.
"""

from __future__ import annotations

import json
import os

import pytest
from recadbench_run.compose import ComposeError, ComposeRequest, compose
from recadbench_run.finish import clean_env, finish
from recadbench_run.gate_hook import STATE_FILE, Gate, digest_path
from runner_helpers import content_repos, git, mini_run, plan_doc, write_plan

PART = "syn_part"
BOX = ("from build123d import BuildPart, BuildSketch, Plane, Rectangle, extrude\n"
       "w = 20.0  # IR parameter\n"
       "with BuildPart() as bp:\n"
       "    with BuildSketch(Plane.XY):\n"
       "        Rectangle(w, 10)\n"
       "    extrude(amount=5)\n"
       "part = bp.part\n")
GT_LIKE = "C:/Users/someone/rcb/ReCADBench-gt/parts/p/gt.step"


@pytest.fixture()
def repos(tmp_path, no_gt_env):
    return content_repos(tmp_path)


def t_run(tmp_path, repos, name="r1", **kw):
    base = dict(strategy="oracle", skillset="build123d@v1", part_id=PART,
                out=tmp_path / "runs" / name, seed_plan=plan_doc(PART), **repos)
    base.update(kw)
    return compose(ComposeRequest(**base))


def write_part(run, src=BOX):
    (run / "cad" / "part.py").write_text(src, encoding="utf-8", newline="\n")


def gated(run, src=BOX):
    write_part(run, src)
    d = Gate(run)("mcp__build123d__execute_file", {"path": "cad/part.py"})
    assert d.allow, d.reason
    return d


# ================================================================ blocker: finish
def test_finish_refuses_a_native_file_the_gate_never_executed(tmp_path, repos):
    pytest.importorskip("build123d")
    run = t_run(tmp_path, repos)
    write_part(run)  # written, never executed through the gate
    res = finish(run)
    assert not res["ok"]
    assert "never executed through the gate" in res["finish"]["error"]
    assert not (run / "out" / "output.step").exists()


def test_finish_refuses_a_source_changed_after_the_gated_execution(tmp_path, repos):
    pytest.importorskip("build123d")
    run = t_run(tmp_path, repos)
    gated(run)
    write_part(run, BOX.replace("w = 20.0", "w = 30.0"))  # edit after the last execution
    res = finish(run)
    assert not res["ok"] and "changed after the last gated execution" in res["finish"]["error"]
    assert not (run / "out" / "output.step").exists()


def test_finish_runs_native_code_sandboxed_without_secrets(tmp_path, repos, monkeypatch):
    """Code the gate lets through (``import os`` is no scan access) still runs in finish only
    under the adapter sandbox, with no GT dir in its environment."""
    pytest.importorskip("build123d")
    run = t_run(tmp_path, repos)
    leak = ("import os\nfrom build123d import Box\n"
            "n = len(os.environ.get('RECADBENCH' + '_GT_DIR', ''))\npart = Box(1 + n, 1, 1)\n")
    gated(run, leak)
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(tmp_path / "gtstore"))
    res = finish(run)
    assert not res["ok"]
    assert "sandbox" in res["finish"]["export"]["error"]
    assert not (run / "out" / "output.step").exists()
    env = clean_env()
    assert "RECADBENCH_GT_DIR" not in env and env["RECAD_SANDBOX"] == "1"
    assert "PYTHONPATH" not in env


def test_finish_ignores_a_planted_recad_cli_package(tmp_path, repos):
    pytest.importorskip("build123d")
    run = t_run(tmp_path, repos)
    gated(run)
    assert not Gate(run).check("Write", {"file_path": "recad_cli/__main__.py",
                                         "content": "x"}).allow
    pkg = run / "recad_cli"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import sys, json, pathlib\na = sys.argv[1:]\n"
        "pathlib.Path(a[a.index('--out') + 1]).write_text('FORGED')\n"
        "print(json.dumps({'ok': True, 'count': 1, 'unmapped': 0, 'solids': 1}))\n",
        encoding="utf-8")
    res = finish(run)
    assert res["ok"], res
    assert (run / "out" / "output.step").read_text(encoding="utf-8").startswith("ISO-10303-21")
    assert json.loads((run / "out" / "features.json").read_text(encoding="utf-8"))["count"] == 1


def test_finish_refuses_a_tampered_gate_state(tmp_path, repos):
    pytest.importorskip("build123d")
    run = t_run(tmp_path, repos)
    gated(run)
    st = run / STATE_FILE
    doc = json.loads(st.read_text(encoding="utf-8"))
    doc["executed"]["fingerprint"] = "0" * 64
    st.write_text(json.dumps(doc), encoding="utf-8")
    res = finish(run)
    assert not res["ok"] and "not written by the gate" in res["finish"]["error"]


# ================================================================ gate state integrity
def test_gate_state_overwrite_or_delete_is_detected(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    write_part(run)
    g = Gate(run)
    assert g("mcp__build123d__execute_file", {"path": "cad/part.py"}).allow
    assert digest_path(run).is_file() and not str(digest_path(run)).startswith(str(run))
    (run / STATE_FILE).write_text(json.dumps({"executed": None, "history": []}),
                                  encoding="utf-8")
    assert g.state().get("tampered")
    d = g("mcp__build123d__execute_file", {"path": "cad/part.py"})
    assert not d.allow and d.category == "tampered"
    (run / STATE_FILE).unlink()
    assert g.state().get("tampered")


def test_compose_starts_the_gate_state_over(tmp_path, repos):
    run = t_run(tmp_path, repos)
    gated(run)
    run2 = t_run(tmp_path, repos, force=True)
    assert run2 == run and not Gate(run).state().get("tampered")
    assert Gate(run).state()["executed"] is None


# ================================================================ revision rule
@pytest.mark.parametrize("tool,args", [
    ("mcp__build123d__execute", {"code": "from build123d import *\nx = Box(30, 30, 30)\n"}),
    ("mcp__build123d__restore_snapshot", {"name": "s"}),
    ("mcp__build123d__edit_feature", {"name": "x"}),
    ("mcp__build123d__reset", {}),
])
def test_every_mutating_tool_is_held_by_the_revision_rule(tmp_path, tool, args):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    write_part(run)
    g = Gate(run)
    assert g("mcp__build123d__execute_file", {"path": "cad/part.py"}).allow
    write_part(run, BOX.replace("w = 20.0", "w = 30.0"))
    d = g(tool, args)
    assert not d.allow and d.category == "revision", d.reason
    assert g("mcp__build123d__measure", {}).allow  # read-only tools stay usable
    write_plan(run, 2)
    assert g(tool, args).allow
    assert g.state()["executed"]["revision"] == 1  # only source tools record an execution


# ================================================================ MCP write arguments
@pytest.mark.parametrize("tool,args", [
    ("mcp__build123d__script", {"save_to": ".recadbench/gate_state.json"}),
    ("mcp__build123d__script", {"save_to": ".claude/settings.json"}),
    ("mcp__build123d__script", {"save_to": "lib/b3dhelpers/__init__.py"}),
    ("mcp__build123d__script", {"save_to": "run_manifest.json"}),
    ("mcp__build123d__script", {"save_to": "CLAUDE.md"}),
    ("mcp__build123d__script", {"save_to": "recad_cli/__main__.py"}),
    ("mcp__build123d__export", {"filename": "input/input.stl", "format": "stl"}),
    ("mcp__build123d__export", {"filename": "_shared/x.step"}),
    ("mcp__build123d__render_view", {"save_to": "input/task.png"}),
    ("mcp__build123d__crop_drawing", {"image_path": "plan/x.png", "bbox_px": [0, 0, 1, 1]}),
    ("mcp__build123d__install_skill", {}),
    ("mcp__retk__render", {"mesh_path": "input/input.stl"}),
])
def test_mcp_tools_cannot_write_protected_files(tmp_path, tool, args):
    run = mini_run(tmp_path, retk=("render",))
    write_plan(run, 1)
    d = Gate(run).check(tool, args)
    assert not d.allow, (tool, args)


def test_mcp_writes_to_executed_plan_and_allowed_folders(tmp_path):
    run = mini_run(tmp_path, retk=("render",))
    write_plan(run, 1)
    write_part(run)
    g = Gate(run)
    assert g("mcp__build123d__execute_file", {"path": "cad/part.py"}).allow
    d = g.check("mcp__build123d__script", {"save_to": "plan/re_plan.v1.json"})
    assert not d.allow and d.category == "path" and "already executed" in d.reason
    assert g.check("mcp__build123d__export", {"filename": "out/candidate.step"}).allow
    assert g.check("mcp__build123d__render_view", {"save_to": "work/iso.png"}).allow
    assert g.check("mcp__retk__render", {"mesh_path": "input/input.stl",
                                         "out_dir": "plan/renders"}).allow


def test_file_tools_write_only_the_writable_folders(tmp_path):
    g = Gate(mini_run(tmp_path))
    for rel in ("recad_cli/__init__.py", "x.py", "notes.md", "sitecustomize.py"):
        assert not g.check("Write", {"file_path": rel, "content": "x"}).allow, rel
    for rel in ("plan/analysis.md", "cad/part.py", "out/candidate.stl", "work/n.md"):
        assert g.check("Write", {"file_path": rel, "content": "x"}).allow, rel


# ================================================================ path arguments
def test_long_path_arguments_are_checked(tmp_path):
    run = mini_run(tmp_path, retk=("mesh_stats", "self_deviation"))
    write_plan(run, 1)
    g = Gate(run)
    outside = str(tmp_path.parent / "gtstore" / "p")
    long_gt = outside + "/" + "./" * 2100 + "gt.step"
    assert not g.check("mcp__retk__mesh_stats", {"mesh_path": long_gt}).allow
    assert not g.check("mcp__retk__self_deviation", {"candidate_path": long_gt,
                                                     "mesh_path": "input/input.stl"}).allow
    long_out = outside + "/" + "./" * 2100 + "x.step"
    assert not g.check("mcp__build123d__export", {"filename": long_out}).allow
    # a long in-run path is refused too (path arguments are capped)
    assert not g.check("Read", {"file_path": "./" * 600 + "input/task.yaml"}).allow
    assert g.check("Read", {"file_path": "input/task.yaml"}).allow


# ================================================================ code literals
@pytest.mark.parametrize("code", [
    f"from build123d import import_step\ngt = import_step(r'{GT_LIKE}')\n",
    "from build123d import import_step\ngt = import_step('../../../../ReCADBench-gt/x/gt.step')\n",
    "import io\nt = io.open(r'C:\\Users\\someone\\secret.json').read()\n",
    "import io\nt = io.open('../../other_run/plan/re_plan.v1.json').read()\n",
    "import io\nio.open('.recadbench/gate_state.json', 'w').write('{}')\n",
    "x = '~/rcb'\n",
    "p = 'gt_plan.json'\n",
])
def test_code_naming_paths_outside_or_gt_is_denied(tmp_path, code):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    g = Gate(run)
    d = g.check("mcp__build123d__execute", {"code": code})
    assert not d.allow and d.category == "scan_code", code
    write_part(run, code)
    assert not g.check("mcp__build123d__execute_file", {"path": "cad/part.py"}).allow


def test_ordinary_literals_stay_allowed(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    code = ("import re\nfrom build123d import *\nrx = re.compile(r'\\d+\\s*x')\n"
            "label = ' // '.join(['a', 'b']) + '/'\npath = 'cad/part.py'\n"
            "out = 'out/candidate.step'\nratio = 'mm/s'\npart = Box(1, 1, 1)\n")
    assert Gate(run).check("mcp__build123d__execute", {"code": code}).allow


# ================================================================ NX journals
@pytest.mark.parametrize("journal", [
    "import runpy\nrunpy.run_path('plan/notes.txt')\n",
    "import builtins\nbuiltins.exec(open('plan/notes.txt').read())\n",
    "c = compile(open('plan/notes.txt').read(), 'n', 'exec')\n",
    "import subprocess\nsubprocess.run(['cmd', '/c', 'dir'])\n",
    "import os\nos.system('dir')\n",
    "import os\nprint(os.listdir(os.path.expanduser('~/rcb/ReCADBench-gt/parts')))\n",
    "import ctypes\n",
    "import pickle\npickle.loads(b'')\n",
])
def test_nx_journal_escapes_are_denied(tmp_path, journal):
    run = mini_run(tmp_path, "nx")
    write_plan(run, 1)
    (run / "plan" / "notes.txt").write_text("import NXOpen.Facet\n", encoding="utf-8")
    (run / "cad" / "j.py").write_text(journal, encoding="utf-8")
    d = Gate(run).check("Bash", {"command": "recadbench nx-run cad/j.py"})
    assert not d.allow, journal


def test_nx_ordinary_journal_stays_allowed(tmp_path):
    from recadbench_run.agents.replay_scripts import NX_SMOKE_JOURNAL

    run = mini_run(tmp_path, "nx")
    write_plan(run, 1)
    (run / "cad" / "step_01.py").write_text(
        NX_SMOKE_JOURNAL + "import os, re, math\nname = os.path.join('cad', 'part.prt')\n"
        "rx = re.compile(r'^\\s*M(\\d+)')\n", encoding="utf-8")
    assert Gate(run).check("Bash", {"command": "recadbench nx-run cad/step_01.py"}).allow


# ================================================================ compose labels / pins
def test_replay_runs_are_labelled_replay(tmp_path, repos):
    run = t_run(tmp_path, repos, agent_backend="replay")
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["model"] == {"id": "replay", "provider": "replay"}
    assert "_replay_" in m["run_id"]
    with pytest.raises(ComposeError, match="replay"):
        t_run(tmp_path, repos, name="r2", agent_backend="replay", model="claude-sonnet-5-5")
    with pytest.raises(ComposeError, match="reserved"):
        t_run(tmp_path, repos, name="r3", model="replay")
    m4 = json.loads((t_run(tmp_path, repos, name="r4") / "run_manifest.json").read_text())
    assert m4["model"] == {"id": "claude-sonnet-5-5", "provider": "anthropic"}


def test_skillset_ref_must_be_the_tag_commit(tmp_path, repos):
    sk = repos["skills_dir"]
    (sk / "build123d" / "skills" / "build123d-modeling" / "SKILL.md").write_text(
        "---\nname: build123d-modeling\ndescription: y\n---\n# changed\n", encoding="utf-8")
    git(sk, "commit", "-qam", "after the tag")
    with pytest.raises(ComposeError, match="tag build123d-v1"):
        t_run(tmp_path, repos, skillset_ref="HEAD")
    run = t_run(tmp_path, repos, name="ok", skillset_ref="build123d-v1")  # same commit: fine
    assert run.is_dir()
    git(sk, "tag", "-d", "build123d-v1")
    run2 = t_run(tmp_path, repos, name="untagged", skillset_ref="HEAD")
    m = json.loads((run2 / "run_manifest.json").read_text(encoding="utf-8"))
    assert any("tag build123d-v1 does not exist" in w for w in m["compose"]["warnings"])


def test_default_harness_level_is_h1(tmp_path, repos):
    run = compose(ComposeRequest(strategy="B", skillset="build123d@v1", part_id=PART,
                                 out=tmp_path / "runs" / "b", **repos))
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["harness_level"] == "H1"
    assert m["compose"]["retk_tools"] == ["mesh_stats", "self_deviation"]


def test_track_t_refuses_a_test_split_part(tmp_path, repos):
    data = repos["data_dir"]
    (data / "splits" / "dev.txt").write_text("", encoding="utf-8")
    (data / "splits" / "test.txt").write_text(PART + "\n", encoding="utf-8")
    with pytest.raises(ComposeError, match="dev parts only"):
        t_run(tmp_path, repos)


def test_inject_gt_plan_must_match_the_track(tmp_path, repos):
    import yaml

    st = repos["strategies_dir"]
    p = st / "strategies" / "B" / "strategy.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["inject_gt_plan"] = True
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    git(st, "commit", "-qam", "B injects")
    with pytest.raises(ComposeError, match="inject_gt_plan"):
        compose(ComposeRequest(strategy="B", skillset="build123d@v1", part_id=PART,
                               out=tmp_path / "runs" / "b", **repos))


def test_system_prompt_indexes_reference_files(tmp_path, repos):
    run = t_run(tmp_path, repos)
    text = (run / "system_prompt.md").read_text(encoding="utf-8")
    assert "# Skill file `.claude/skills/build123d-modeling/SKILL.md`" in text
    assert "Skill file `.claude/skills/build123d-modeling/references/r.md`" not in text
    assert "`.claude/skills/build123d-modeling/references/r.md` (" in text


# ================================================================ agent backends / CLI
def test_two_step_agent_then_finish_keeps_efficiency(tmp_path, repos):
    from recadbench_run import agents

    run = t_run(tmp_path, repos, agent_backend="replay")
    script = tmp_path / "s.json"
    script.write_text(json.dumps([{"tool": "Read", "args": {"file_path": "input/task.yaml"}},
                                  {"tool": "Bash", "args": {"command": "dir"},
                                   "expect": "deny"}]), encoding="utf-8")
    res = agents.get("replay").run(run, script=str(script))
    assert res.ok
    eff = json.loads((run / "efficiency.json").read_text(encoding="utf-8"))
    assert eff["turns"] == 2 and eff["gate_denials"] == 1
    finish(run)  # a separate step: must not reset the counters
    eff = json.loads((run / "efficiency.json").read_text(encoding="utf-8"))
    assert eff["turns"] == 2 and eff["gate_denials"] == 1


def test_unknown_replay_script_is_a_clean_cli_error(tmp_path, repos, capsys):
    from recadbench_run.cli import main

    run = t_run(tmp_path, repos, agent_backend="replay")
    rc = main(["agent", "--run-dir", str(run), "--script", "oracle_nope"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 2 and not out["ok"] and "unknown replay script" in out["error"]


def test_nx_smoke_replay_script_is_built_in():
    from recadbench_run.agents.replay_scripts import SCRIPTS, load

    assert "nx_smoke" in SCRIPTS and load("nx_smoke") is SCRIPTS["nx_smoke"]


def test_digest_dir_is_outside_the_run(tmp_path, monkeypatch):
    """The digest dir can be pinned (RECADBENCH_GATE_DIGEST_DIR); default is outside the run,
    where every gated path argument is denied."""
    monkeypatch.setenv("RECADBENCH_GATE_DIGEST_DIR", str(tmp_path / "dg"))
    run = mini_run(tmp_path)
    assert digest_path(run).parent == tmp_path / "dg"
    monkeypatch.delenv("RECADBENCH_GATE_DIGEST_DIR")
    assert digest_path(run).parent == run.parent / ".recadbench-gate"
    rel = os.path.relpath(digest_path(run), run)
    assert not Gate(run).check("Read", {"file_path": rel}).allow
