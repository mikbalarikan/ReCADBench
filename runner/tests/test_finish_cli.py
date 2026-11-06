"""finish (run-dir contract of evaluator/recadbench_eval/README.md) and the recadbench CLI."""

from __future__ import annotations

import hashlib
import io
import json

import pytest
from recadbench_run.cli import main
from recadbench_run.compose import ComposeRequest, compose
from recadbench_run.finish import finish
from runner_helpers import content_repos, plan_doc

BOX = ("from build123d import BuildPart, BuildSketch, Plane, Rectangle, extrude\n"
       "w = 20.0  # IR parameter\n"
       "with BuildPart() as bp:\n"
       "    with BuildSketch(Plane.XY):\n"
       "        Rectangle(w, 10)\n"
       "    extrude(amount=5)\n"
       "part = bp.part\n")


@pytest.fixture()
def run(tmp_path, no_gt_env):
    repos = content_repos(tmp_path)
    return compose(ComposeRequest(strategy="oracle", skillset="build123d@v1",
                                  part_id="syn_part", out=tmp_path / "runs" / "f1",
                                  seed_plan=plan_doc("syn_part"), **repos))


def test_finish_extracts_exports_and_hashes(run):
    pytest.importorskip("build123d")
    (run / "cad" / "part.py").write_text(BOX, encoding="utf-8", newline="\n")
    (run / "out" / "output.step").write_text("agent self-report", encoding="utf-8")
    (run / "out" / "features.json").write_text("{}", encoding="utf-8")
    res = finish(run, efficiency={"turns": 3, "tokens_in": 10})
    assert res["ok"], res
    f = res["finish"]
    assert sorted(f["discarded"]) == ["out/features.json", "out/output.step"]
    assert f["extract"]["ok"] and f["extract"]["count"] == 1
    assert f["export"]["ok"] and f["export"]["solids"] == 1
    assert f["export"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    feats = json.loads((run / "out" / "features.json").read_text(encoding="utf-8"))
    assert [x["taxonomy_type"] for x in feats["features"]] == ["extrude"]
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["native_sha256"] == hashlib.sha256((run / "cad" / "part.py").read_bytes()
                                                ).hexdigest()
    assert m["hashes"]["out/output.step"] == hashlib.sha256(
        (run / "out" / "output.step").read_bytes()).hexdigest()
    assert json.loads((run / "efficiency.json").read_text(encoding="utf-8"))["turns"] == 3
    assert res.get("score") is None


def test_finish_without_native_file(run):
    res = finish(run)
    assert not res["ok"] and "native file cad/part.py missing" in res["finish"]["error"]
    assert (run / "efficiency.json").is_file()


def test_finish_score_skips_without_gt(run):
    from recadbench_run.finish import run_score

    assert run_score(run, "syn_part")["skipped"].startswith("RECADBENCH_GT_DIR")


def test_cli_compose_and_gate(tmp_path, no_gt_env, capsys, monkeypatch):
    repos = content_repos(tmp_path)
    out = tmp_path / "runs" / "c1"
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(plan_doc("syn_part")), encoding="utf-8")
    rc = main(["compose", "--strategy", "oracle", "--skillset", "build123d@v1", "--part",
               "syn_part", "--seed-plan", str(seed), "--out", str(out),
               "--skills-dir", str(repos["skills_dir"]), "--strategies-dir",
               str(repos["strategies_dir"]), "--data-dir", str(repos["data_dir"]),
               "--budget", "max_turns=12"])
    assert rc == 0, capsys.readouterr().out
    assert json.loads(capsys.readouterr().out)["ok"]
    m = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["budgets"]["max_turns"] == 12
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "mcp__build123d__find_holes", "tool_input": {}})))
    assert main(["gate", "--run-dir", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "permissionDecision"] == "deny"
    rc = main(["compose", "--strategy", "oracle", "--skillset", "build123d@v1", "--part",
               "syn_part", "--out", str(tmp_path / "runs" / "c2"),
               "--skills-dir", str(repos["skills_dir"]), "--strategies-dir",
               str(repos["strategies_dir"]), "--data-dir", str(repos["data_dir"])])
    assert rc == 1 and "seed-plan" in json.loads(capsys.readouterr().out)["error"]


def test_cli_forwards_nx_run(capsys):
    assert main(["nx-run", "--help"]) == 0
    assert "recadbench nx-run" in capsys.readouterr().out


def test_cli_seed_plan_needs_gt(tmp_path, no_gt_env, capsys):
    assert main(["seed-plan", "--part", "x", "--out", str(tmp_path / "s.json")]) == 1
    assert "RECADBENCH_GT_DIR" in json.loads(capsys.readouterr().out)["error"]
