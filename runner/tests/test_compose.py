"""compose (00_DECISIONS D6) on synthetic content repos: layout, pins, tracks, GT hygiene."""

from __future__ import annotations

import json
import shutil

import pytest
from recadbench_run.artifacts import gt_leak_check
from recadbench_run.compose import ComposeError, ComposeRequest, compose
from recadbench_run.render_prompt import render
from recadbench_run.sources import SourceError
from runner_helpers import content_repos, git, plan_doc

PART = "syn_part"


@pytest.fixture()
def repos(tmp_path, no_gt_env):
    return content_repos(tmp_path)


def req(tmp_path, repos, **kw) -> ComposeRequest:
    base = dict(strategy="oracle", skillset="build123d@v1", part_id=PART,
                out=tmp_path / "runs" / kw.pop("name", "r1"), **repos)
    base.update(kw)
    return ComposeRequest(**base)


def seed() -> dict:
    return plan_doc(PART)


def test_track_t_layout_and_pins(tmp_path, repos):
    run = compose(req(tmp_path, repos, seed_plan=seed()))
    for rel in ("CLAUDE.md", "system_prompt.md", "run_manifest.json", ".mcp.json",
                ".claude/settings.json", ".claude/skills/strategy-oracle/SKILL.md",
                ".claude/skills/strategy-oracle/playbook.md",
                ".claude/skills/strategy-oracle/stages/intake.md",
                ".claude/skills/strategy-oracle/shared/ir_authoring.md",
                ".claude/skills/build123d-modeling/SKILL.md",
                ".claude/skills/build123d-modeling/references/r.md",
                ".claude/agents/build123d-modeler.md", ".claude/examples/e01/part.py",
                "lib/b3dhelpers/__init__.py", "_shared/ir_execution.md",
                "_shared/single_solid_rules.md", "input/input.stl", "input/task.yaml",
                "plan/re_plan.v1.json", ".recadbench/gate.json", ".recadbench/backend.yaml"):
        assert (run / rel).is_file(), rel
    assert (run / "input" / "input.stl").read_bytes() == (
        repos["data_dir"] / "parts" / PART / "input.stl").read_bytes()
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["track"] == "T" and m["split"] == "dev"
    assert m["skillset"]["git_sha"] == git(repos["skills_dir"], "rev-parse", "build123d-v1")
    assert m["strategy"]["git_sha"].startswith(git(repos["strategies_dir"], "rev-parse", "HEAD"))
    assert m["budgets"] == {"max_wall_s": 900, "max_turns": 40}
    from recadbench_spec import validate_document

    assert validate_document(m, "run_manifest").ok
    mcp = json.loads((run / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(mcp) == {"build123d"}  # oracle: empty toolkit_tools -> no retk at all
    assert mcp["build123d"]["env"]["PYTHONPATH"].endswith("lib")
    hook = json.loads((run / ".claude/settings.json").read_text(encoding="utf-8"))
    cmd = hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "recadbench_run.gate_hook" in cmd and str(run) in cmd
    gate = json.loads((run / ".recadbench/gate.json").read_text(encoding="utf-8"))
    assert gate["seeded_revisions"] == [1] and gate["native_file"] == "cad/part.py"
    text = (run / "CLAUDE.md").read_text(encoding="utf-8")
    for s in ("## STRATEGY", "## SKILLSET", "## Gate", "## Conflict rule", "strategy-oracle"):
        assert s in text
    assert render(run) == (run / "system_prompt.md").read_text(encoding="utf-8")


def test_track_e_ships_no_plan_and_retk_subset(tmp_path, repos):
    run = compose(req(tmp_path, repos, strategy="B", harness_level="H2"))
    assert not any((run / "plan").iterdir())
    mcp = json.loads((run / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    # B lists mesh_stats, slice, fit_primitives, self_deviation, render; H2 allows 3 of them
    assert mcp["retk"]["args"] == ["--tools", "mesh_stats,self_deviation,render"]
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["track"] == "E" and m["harness_level"] == "H2"


def test_track_s_plan_only(tmp_path, repos):
    run = compose(req(tmp_path, repos, strategy="B", skillset="none", backend="none"))
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["track"] == "S" and m["backend"]["id"] == "none"
    assert not (run / "lib").exists() and not (run / ".recadbench" / "backend.yaml").exists()
    assert set(json.loads((run / ".mcp.json").read_text())["mcpServers"]) == {"retk"}
    assert not any((run / "plan").iterdir())


def test_none_skillset_baseline(tmp_path, repos):
    run = compose(req(tmp_path, repos, skillset="none", backend="build123d",
                      seed_plan=seed()))
    assert (run / "_shared" / "ir_execution.md").is_file()
    assert not (run / "_shared" / "single_solid_rules.md").exists()
    assert not (run / "lib").exists()
    assert [p.name for p in (run / ".claude" / "skills").iterdir()] == ["strategy-oracle"]
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["skillset"] == {"id": "none", "git_sha": "none"}


@pytest.mark.parametrize("kw,match", [
    ({"seed_plan": None}, "seed-plan"),
    ({"strategy": "B", "seed_plan": seed()}, "only for Track T"),
    ({"track": "E"}, "oracle"),
    ({"skillset": "build123d@v2", "seed_plan": seed()}, "tag build123d-v2"),
    ({"skillset": "nx@v1", "backend": "build123d"}, "contradicts"),
    ({"strategy": "B", "skillset": "none", "backend": "none", "track": "E"}, "Track E"),
    ({"part_id": "missing", "seed_plan": seed()}, "no task.yaml"),
])
def test_compose_refuses(tmp_path, repos, kw, match):
    with pytest.raises((ComposeError, SourceError), match=match):
        compose(req(tmp_path, repos, **kw))


def test_seed_must_be_this_part(tmp_path, repos):
    with pytest.raises(ComposeError, match="revision 1 of this part"):
        compose(req(tmp_path, repos, seed_plan=plan_doc("other")))


def test_ref_pins_content_not_worktree(tmp_path, repos):
    """With a ref, compose copies the committed file even when the checkout differs."""
    sk = repos["skills_dir"] / "build123d/skills/build123d-modeling/SKILL.md"
    committed = sk.read_text(encoding="utf-8")
    sk.write_text("dirty edit\n", encoding="utf-8")
    run = compose(req(tmp_path, repos, seed_plan=seed()))
    assert (run / ".claude/skills/build123d-modeling/SKILL.md").read_text(
        encoding="utf-8") == committed
    run2 = compose(req(tmp_path, repos, strategy="B", name="r2"))
    m = json.loads((run2 / "run_manifest.json").read_text(encoding="utf-8"))
    assert not m["strategy"]["git_sha"].endswith("-dirty")
    (repos["strategies_dir"] / "strategies/B/playbook.md").write_text("x\n", encoding="utf-8")
    run3 = compose(req(tmp_path, repos, strategy="B", name="r3"))
    m3 = json.loads((run3 / "run_manifest.json").read_text(encoding="utf-8"))
    assert m3["strategy"]["git_sha"].endswith("-dirty")


def test_existing_run_dir_needs_force(tmp_path, repos):
    compose(req(tmp_path, repos, seed_plan=seed()))
    with pytest.raises(ComposeError, match="--force"):
        compose(req(tmp_path, repos, seed_plan=seed()))
    compose(req(tmp_path, repos, seed_plan=seed(), force=True))


# ------------------------------------------------------------------------- GT hygiene
def _gt_store(tmp_path, repos):
    gt = tmp_path / "gt"
    (gt / PART).mkdir(parents=True)
    gp = plan_doc(PART)
    gp["datums"][0]["origin"] = [1.0, 2.0, 3.0]  # GT frame differs from the mapped seed
    (gt / PART / "gt_plan.json").write_text(json.dumps(gp), encoding="utf-8")
    (gt / PART / "key_dims.yaml").write_text("dims: []\n", encoding="utf-8")
    (gt / PART / "meta.yaml").write_text("source_kind: scan_rebuild\nsplit: dev\n",
                                         encoding="utf-8")
    (gt / PART / "gt.step").write_text("ISO-10303-21; synthetic", encoding="utf-8")
    shutil.copyfile(repos["data_dir"] / "parts" / PART / "input.stl",
                    gt / PART / "scan_raw.stl")
    return gt, gp


def test_gt_leak_check_clean_run(tmp_path, repos, monkeypatch):
    gt, _ = _gt_store(tmp_path, repos)
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(gt))
    run = compose(req(tmp_path, repos, seed_plan=seed()))  # check runs inside compose
    assert gt_leak_check(run, gt, PART, allow_seed=True) == []
    run_e = compose(req(tmp_path, repos, strategy="B", name="e"))
    assert gt_leak_check(run_e, gt, PART) == []
    # no GT path anywhere in the run dir (agent env strips RECADBENCH_GT_DIR as well)
    for p in run.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".md", ".yaml"):
            assert str(gt) not in p.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("leak", ["copy_step", "gt_plan_json", "plan_in_track_e",
                                  "path", "renamed_key_dims"])
def test_gt_leak_check_finds_leaks(tmp_path, repos, leak):
    gt, gp = _gt_store(tmp_path, repos)
    run = compose(req(tmp_path, repos, strategy="B"))
    if leak == "copy_step":
        shutil.copyfile(gt / PART / "gt.step", run / "cad" / "ref.step")
    elif leak == "gt_plan_json":
        (run / "cad" / "notes.json").write_text(json.dumps(gp, indent=1), encoding="utf-8")
    elif leak == "plan_in_track_e":
        (run / "plan" / "re_plan.v1.json").write_text(json.dumps(seed()), encoding="utf-8")
    elif leak == "path":
        (run / "cad" / "notes.md").write_text(f"see {gt}\n", encoding="utf-8")
    else:
        shutil.copyfile(gt / PART / "key_dims.yaml", run / "cad" / "dims.txt")
    assert gt_leak_check(run, gt, PART), leak


def test_compose_refuses_a_leaking_run(tmp_path, repos, monkeypatch):
    gt, gp = _gt_store(tmp_path, repos)
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(gt))
    with pytest.raises(ComposeError, match="ground truth"):
        compose(req(tmp_path, repos, seed_plan=gp))  # the unmapped GT plan as "seed"
    assert not (tmp_path / "runs" / "r1").exists()


def test_agent_env_strips_secrets(tmp_path, monkeypatch):
    from recadbench_run.compose import agent_env

    monkeypatch.setenv("RECADBENCH_GT_DIR", "/gt")
    monkeypatch.setenv("RECADBENCH_GH_TOKEN", "x")
    env = agent_env(tmp_path)
    assert "RECADBENCH_GT_DIR" not in env and "RECADBENCH_GH_TOKEN" not in env
    assert env["RECADBENCH_RUN_DIR"] == str(tmp_path)
