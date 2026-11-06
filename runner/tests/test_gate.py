"""gate_hook: every rule with a known decision (synthetic run dirs, the repo's real backend.yaml).

H8 required cases: the hook blocks CAD without a plan, blocks shell, allows
`recadbench nx-run cad/x.py` only for nx, and requires N+1 after a CAD change.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil

import pytest
import yaml
from recadbench_run.gate_hook import Gate, hook_output, main, make_sdk_hook, plan_status
from runner_helpers import BACKENDS, mini_run, write_plan

PART = "from build123d import *\nwith BuildPart() as bp:\n    Box(10, 20, 5)\npart = bp.part\n"


def gate(tmp_path, backend="build123d", **kw) -> tuple[Gate, object]:
    run = mini_run(tmp_path, backend, **kw)
    return Gate(run), run


# --------------------------------------------------------------------- plan before CAD
def test_blocks_cad_without_plan(tmp_path):
    g, run = gate(tmp_path)
    d = g("mcp__build123d__execute", {"code": "Box(1, 1, 1)"})
    assert not d.allow and d.category == "plan" and "re_plan.v1.json" in d.reason
    d = g("mcp__build123d__measure", {})
    assert not d.allow and d.category == "plan"


def test_blocks_cad_with_invalid_plan(tmp_path):
    g, run = gate(tmp_path)
    (run / "plan" / "re_plan.v1.json").write_text('{"spec_version": "0.3.0"}', encoding="utf-8")
    d = g("mcp__build123d__measure", {})
    assert not d.allow and "not schema-valid" in d.reason


def test_blocks_plan_of_another_part_and_gaps(tmp_path):
    g, run = gate(tmp_path)
    write_plan(run, 1, part_id="other_part")
    assert "part_id" in g("mcp__build123d__measure", {}).reason
    (run / "plan" / "re_plan.v1.json").unlink()
    write_plan(run, 2)
    assert "contiguous" in g("mcp__build123d__measure", {}).reason


def test_allows_cad_with_valid_plan(tmp_path):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    assert g("mcp__build123d__measure", {}).allow
    assert g("mcp__build123d__execute", {"code": "Box(1, 1, 1)"}).allow


def test_plan_status_reports_highest_revision(tmp_path):
    run = mini_run(tmp_path)
    write_plan(run, 1)
    write_plan(run, 2)
    st = plan_status(run, "syn_part")
    assert st.ok and st.revision == 2


# --------------------------------------------------------------------------- shell
@pytest.mark.parametrize("cmd", ["ls", "python -c 1", "recadbench nx-run cad/x.py",
                                 "cat input/input.stl"])
def test_blocks_shell_for_mcp_backend(tmp_path, cmd):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    d = g("Bash", {"command": cmd})
    assert not d.allow and d.category == "shell"
    assert not g("PowerShell", {"command": cmd}).allow


def test_nx_allows_only_the_runner_command(tmp_path):
    g, run = gate(tmp_path, "nx")
    write_plan(run, 1)
    (run / "cad" / "x.py").write_text("import NXOpen\n", encoding="utf-8")
    assert g("Bash", {"command": "recadbench nx-run cad/x.py"}).allow
    assert g("Bash", {"command": "recadbench nx-run cad/x.py --timeout 300"}).allow
    for bad in ("recadbench nx-run cad/x.py; ls", "recadbench nx-run cad/x.py && dir",
                "recadbench nx-run cad/../x.py", "recadbench nx-run lib/x.py",
                "recadbench nx-run cad/x.py --part cad/other.prt",
                "recadbench nx-run cad/missing.py", "recad nx export cad/part.prt",
                "recadbench compose --strategy B", "python cad/x.py",
                "recadbench nx-run cad/x.py | tee log", "recadbench nx-run $(echo cad/x.py)",
                "recadbench nx-run cad/x.py --timeout 1e9"):
        d = g("Bash", {"command": bad})
        assert not d.allow, bad


def test_nx_command_needs_plan(tmp_path):
    g, run = gate(tmp_path, "nx")
    (run / "cad" / "x.py").write_text("import NXOpen\n", encoding="utf-8")
    d = g("Bash", {"command": "recadbench nx-run cad/x.py"})
    assert not d.allow and d.category == "plan"


def test_nx_journal_source_is_matched_like_nx_run(tmp_path):
    """kind cli: the deny regex sees the journal SOURCE (nx_run.journal_source, comments
    removed), on the journal and every importable .py of the run dir and lib/."""
    g, run = gate(tmp_path, "nx")
    write_plan(run, 1)
    j = run / "cad" / "x.py"
    j.write_text("import NXOpen\n# FacetedBody in a comment is fine\n", encoding="utf-8")
    assert g("Bash", {"command": "recadbench nx-run cad/x.py"}).allow
    j.write_text("import NXOpen\nb = 'FacetedBody'\n", encoding="utf-8")
    d = g("Bash", {"command": "recadbench nx-run cad/x.py"})
    assert not d.allow and d.category == "denied_tool"
    j.write_text("import NXOpen\n", encoding="utf-8")
    (run / "lib" / "helper.py").write_text("import trimesh\n", encoding="utf-8")
    d = g("Bash", {"command": "recadbench nx-run cad/x.py"})
    assert not d.allow and "lib/helper.py" in d.reason
    (run / "lib" / "helper.py").unlink()
    (run / "lib" / "blob.pyc").write_bytes(b"\0")
    assert not g("Bash", {"command": "recadbench nx-run cad/x.py"}).allow


def test_nx_journal_reading_the_scan_is_denied(tmp_path):
    g, run = gate(tmp_path, "nx")
    write_plan(run, 1)
    (run / "cad" / "x.py").write_text("data = open('input/input.stl', 'rb').read()\n",
                                      encoding="utf-8")
    assert not g("Bash", {"command": "recadbench nx-run cad/x.py"}).allow


# -------------------------------------------------------------- revision rule (N + 1)
def test_requires_next_revision_after_cad_change(tmp_path):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    part = run / "cad" / "part.py"
    part.write_text(PART, encoding="utf-8")
    call = ("mcp__build123d__execute_file", {"path": "cad/part.py", "result_name": "part"})
    assert g(*call).allow
    assert g(*call).allow  # re-running unchanged sources is fine
    assert g("mcp__build123d__execute", {"code": "print(1)"}).allow  # probes do not count
    part.write_text(PART.replace("Box(10", "Box(12"), encoding="utf-8")
    d = g(*call)
    assert not d.allow and d.category == "revision" and "re_plan.v2.json" in d.reason
    write_plan(run, 2)
    assert g(*call).allow
    assert g.state()["executed"]["revision"] == 2
    part.write_text(PART, encoding="utf-8")
    assert "re_plan.v3.json" in g(*call).reason


def test_nx_requires_next_revision_after_journal_change(tmp_path):
    g, run = gate(tmp_path, "nx")
    write_plan(run, 1)
    (run / "cad" / "step_01.py").write_text("import NXOpen\n", encoding="utf-8")
    cmd = {"command": "recadbench nx-run cad/step_01.py"}
    assert g("Bash", cmd).allow
    (run / "cad" / "part.prt").write_bytes(b"binary NX part, not a source")
    (run / "cad" / "last_run.json").write_text("{}", encoding="utf-8")
    assert g("Bash", cmd).allow  # outputs of nx-run are not CAD sources
    (run / "cad" / "step_02.py").write_text("import NXOpen\n", encoding="utf-8")
    assert not g("Bash", {"command": "recadbench nx-run cad/step_02.py"}).allow
    write_plan(run, 2)
    assert g("Bash", {"command": "recadbench nx-run cad/step_02.py"}).allow


def test_executed_and_seeded_revisions_are_read_only(tmp_path):
    g, run = gate(tmp_path, seeded=(1,))
    write_plan(run, 1)
    d = g("Write", {"file_path": str(run / "plan" / "re_plan.v1.json"), "content": "{}"})
    assert not d.allow and "harness" in d.reason
    assert g("Write", {"file_path": "plan/re_plan.v2.json", "content": "{}"}).allow
    write_plan(run, 2)
    (run / "cad" / "part.py").write_text(PART, encoding="utf-8")
    assert g("mcp__build123d__execute_file", {"path": "cad/part.py"}).allow
    assert not g("Edit", {"file_path": "plan/re_plan.v2.json", "old_string": "a",
                          "new_string": "b"}).allow
    assert g("Write", {"file_path": "plan/re_plan.v3.json", "content": "{}"}).allow


# --------------------------------------------------------- denied_tools (backend.yaml)
@pytest.mark.parametrize("tool,args", [
    ("mcp__build123d__import_cad_file", {"path": "input/input.stl"}),
    ("mcp__build123d__mesh_section", {"axis": "Z"}),
    ("mcp__build123d__mesh_holes", {}),
    ("mcp__build123d__find_holes", {}),
    ("mcp__build123d__recognise_features", {}),
    ("mcp__build123d__compare", {"a": "part", "b": "scan"}),
    ("mcp__build123d__execute", {"code": "s = import_stl('input/input.stl')"}),
])
def test_build123d_denied_tools(tmp_path, tool, args):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    d = g(tool, args)
    assert not d.allow and d.category in ("denied_tool", "scan_code")


def test_denied_tools_come_from_backend_yaml(tmp_path):
    """Not hard-coded: removing an entry from the run's backend.yaml allows the tool, adding
    one denies it."""
    g, run = gate(tmp_path)
    write_plan(run, 1)
    assert not g("mcp__build123d__compare", {}).allow
    man = yaml.safe_load((run / ".recadbench" / "backend.yaml").read_text(encoding="utf-8"))
    man["denied_tools"] = [r for r in man["denied_tools"]
                           if r["tool"] != "mcp__build123d__compare"]
    man["denied_tools"].append({"tool": "mcp__build123d__render_view", "reason": "test"})
    (run / ".recadbench" / "backend.yaml").write_text(yaml.safe_dump(man), encoding="utf-8")
    g2 = Gate(run)
    assert g2("mcp__build123d__compare", {}).allow
    assert not g2("mcp__build123d__render_view", {}).allow


@pytest.mark.parametrize("body", [
    "import trimesh\nm = trimesh.load('x')\n",
    "import open3d as o3d\n",
    "from stl import mesh\nm = mesh.Mesh.from_file('scan.stl')\n",
    "from OCP.RWStl import RWStl\n",
    "from OCP.StlAPI import StlAPI_Reader\n",
    "with open('input/input.stl', 'rb') as fh:\n    data = fh.read()\n",
    "s = import_stl('scan.stl')\n",
    "exec(open('cad/x.txt').read())\n",
])
def test_execute_file_body_is_matched(tmp_path, body):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    (run / "cad" / "part.py").write_text(PART + body, encoding="utf-8")
    d = g("mcp__build123d__execute_file", {"path": "cad/part.py", "result_name": "part"})
    assert not d.allow and d.category == "scan_code", d.reason


def test_execute_file_importable_sibling_is_matched(tmp_path):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    (run / "cad" / "part.py").write_text("import helper\n" + PART, encoding="utf-8")
    (run / "cad" / "helper.py").write_text("import pymeshlab\n", encoding="utf-8")
    d = g("mcp__build123d__execute_file", {"path": "cad/part.py"})
    assert not d.allow and "helper.py" in d.reason


def test_ordinary_modelling_code_stays_allowed(tmp_path):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    code = ("points = [(0, 0), (1, 0), (1, 1)]  # mesh of points in a comment\n" + PART +
            "step = 2.5  # a variable named step\n")
    (run / "cad" / "part.py").write_text(code, encoding="utf-8")
    assert g("mcp__build123d__execute_file", {"path": "cad/part.py"}).allow
    assert g("mcp__build123d__execute", {"code": code}).allow
    # fix2: build123d's own writers / readers are not available in agent code (the export tool,
    # with its gated filename, is)
    for line in ("export_stl(part, 'out/preview.stl')", "export_step(part, 'out/x.step')",
                 "p2 = import_step('cad/m.step')", "part.export_brep('out/x.brep')"):
        d = g("mcp__build123d__execute", {"code": PART + line + "\n"})
        assert not d.allow and d.category == "code_policy", line


def test_comments_do_not_trigger_code_patterns(tmp_path):
    g, run = gate(tmp_path)
    write_plan(run, 1)
    # the runner's scan-code patterns ignore comments (backend.yaml args_regex sees the raw
    # JSON arguments, ADR 0004; its own cases are pinned in backends/tests)
    ok = "# from OCP.RWStl import RWStl\nBox(1, 1, 1)"
    assert g("mcp__build123d__execute", {"code": ok}).allow
    d = g("mcp__build123d__execute", {"code": "from OCP.RWStl import RWStl\nBox(1, 1, 1)"})
    assert not d.allow and d.category == "scan_code"


# --------------------------------------------------------------------- paths & tools
def test_paths_outside_the_run_are_denied(tmp_path):
    g, run = gate(tmp_path)
    outside = tmp_path / "secret.json"
    outside.write_text("{}", encoding="utf-8")
    assert not g("Read", {"file_path": str(outside)}).allow
    assert not g("Read", {"file_path": "../secret.json"}).allow
    assert not g("Glob", {"pattern": "**/*.json", "path": str(tmp_path)}).allow
    assert not g("Grep", {"pattern": "x", "path": str(tmp_path)}).allow
    assert g("Read", {"file_path": str(run / "input" / "input.stl")}).allow
    assert g("Glob", {"pattern": "**/*.md"}).allow
    write_plan(run, 1)
    assert not g("mcp__build123d__execute_file", {"path": str(outside)}).allow
    assert not g("mcp__retk__mesh_stats", {"mesh_path": str(outside)}).allow


@pytest.mark.parametrize("rel", ["input/input.stl", "input/task.yaml", "lib/x.py",
                                 ".claude/settings.json", ".mcp.json", "CLAUDE.md",
                                 "run_manifest.json", ".recadbench/gate_state.json",
                                 "_shared/ir_execution.md"])
def test_protected_paths_are_read_only(tmp_path, rel):
    g, run = gate(tmp_path)
    assert not g("Write", {"file_path": rel, "content": "x"}).allow
    assert not g("Edit", {"file_path": str(run / rel), "old_string": "a",
                          "new_string": "b"}).allow


def test_tool_surface(tmp_path):
    g, run = gate(tmp_path, retk=("mesh_stats", "slice"))
    write_plan(run, 1)
    assert g("mcp__retk__mesh_stats", {"mesh_path": "input/input.stl"}).allow
    assert not g("mcp__retk__fit_primitives", {}).allow
    assert not g("mcp__freecad__execute_python", {"code": "1"}).allow
    assert not g("mcp__github__create_issue", {}).allow
    for t in ("WebFetch", "WebSearch", "NotebookEdit", "AskUserQuestion", "SomethingNew"):
        assert not g(t, {}).allow, t
    for t in ("Read", "Glob", "Grep", "TodoWrite", "Task", "Skill"):
        assert g(t, {}).allow, t


def test_plan_only_run_has_no_cad(tmp_path):
    g, run = gate(tmp_path, "none")
    write_plan(run, 1)
    assert not g("mcp__build123d__execute", {"code": "1"}).allow
    assert not g("Bash", {"command": "recadbench nx-run cad/x.py"}).allow
    assert g("Write", {"file_path": "plan/re_plan.v2.json", "content": "{}"}).allow


# -------------------------------------------------------------------------- adapters
def test_hook_main_protocol(tmp_path, monkeypatch, capsys):
    run = mini_run(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(run)})))
    assert main(["--run-dir", str(run)]) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Read", "tool_input": {"file_path": "CLAUDE.md"}})))
    assert main(["--run-dir", str(run)]) == 0
    assert json.loads(capsys.readouterr().out) == {}
    log = (run / ".recadbench" / "gate_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["allow"] for x in log] == [False, True]


def test_hook_fails_closed_without_config(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert main(["--run-dir", str(tmp_path)]) == 2


def test_sdk_hook_adapter(tmp_path):
    g, run = gate(tmp_path)
    seen = []
    hook = make_sdk_hook(g, lambda tool, d: seen.append((tool, d.allow)))
    out = asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "id", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    out = asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}, "id", None))
    assert out == {}
    assert seen == [("Bash", False), ("Read", True)]
    assert hook_output(g("Read", {})) == {}


def test_nx_backend_yaml_is_the_real_one(tmp_path):
    run = mini_run(tmp_path, "nx")
    assert (run / ".recadbench" / "backend.yaml").read_bytes() == (
        BACKENDS / "nx" / "backend.yaml").read_bytes()
    shutil.rmtree(run)


def test_background_tasks_are_denied(tmp_path):
    """H8 smoke run: a background subagent outlived the session; tasks run in the foreground."""
    g, run = gate(tmp_path)
    d = g("Agent", {"prompt": "x", "subagent_type": "build123d-modeler",
                    "run_in_background": True})
    assert not d.allow and d.category == "background"
    assert g("Agent", {"prompt": "x", "subagent_type": "build123d-modeler"}).allow
