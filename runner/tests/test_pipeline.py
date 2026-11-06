"""End to end on a synthetic scan part (CI): seed-plan -> compose -> replay agent (real
build123d-mcp session) -> finish (recad build123d extract/export) -> recadbench-eval score.

Ground truth is the evaluator's synthetic "scan" part (known T_scan_to_gt, scan floor,
observability mask), generated in a temp RECADBENCH_GT_DIR. The input scan is the GT tessellation
moved into the scanner frame with inv(T_scan_to_gt), so the oracle executing the mapped plan must
land on the scan: gate ok, PASS, plan score 100.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from runner_helpers import content_repos, evaluator_conftest, write_scan_stl

PART = "syn_scan"


def _have_b3d_mcp() -> bool:
    scripts = Path(sys.executable).parent
    return any((scripts / n).is_file() for n in ("build123d-mcp", "build123d-mcp.exe")) or \
        bool(shutil.which("build123d-mcp"))


pytestmark = [
    pytest.mark.skipif(not _have_b3d_mcp(), reason="build123d-mcp not installed"),
]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    pytest.importorskip("build123d")
    pytest.importorskip("recadbench_eval")
    ec = evaluator_conftest()
    gt = tmp_path / "gt"
    ec.write_gt(gt, PART, "scan")
    from recadbench_run.frames import invert_rigid

    T_inv = invert_rigid(ec.T_SCAN.tolist())

    def scan(path):
        write_scan_stl(path, ec.build_shape(), T_inv)

    repos = content_repos(tmp_path, PART, scan_writer=scan)
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(gt))
    return {"gt": gt, "repos": repos, "tmp": tmp_path}


def test_oracle_build123d_replay_end_to_end(world):
    from recadbench_run.agents import replay
    from recadbench_run.compose import ComposeRequest, compose
    from recadbench_run.finish import finish
    from recadbench_run.seed import seed_plan_for
    from recadbench_spec import validate_document

    seed, notes = seed_plan_for(PART)
    run = compose(ComposeRequest(strategy="oracle", skillset="build123d@v1", part_id=PART,
                                 out=world["tmp"] / "runs" / "t1", seed_plan=seed,
                                 agent_backend="replay", model="replay", provider="replay",
                                 **world["repos"]))
    res = replay.run(run, "oracle_build123d")
    assert res.ok, res.details
    assert res.efficiency["gate_denials"] == 1  # the scripted scan-import probe
    assert res.efficiency["turns"] == 8
    out = finish(run, efficiency=res.efficiency, score=True)
    assert out["ok"], json.dumps(out["finish"])
    assert out["finish"]["extract"]["ok"] and out["finish"]["extract"]["unmapped"] == 0
    assert out["score"]["ok"], out["score"]
    m = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["native_file"] == "cad/part.py" and len(m["native_sha256"]) == 64
    for rel in ("out/output.step", "out/features.json", "plan/re_plan.v1.json",
                "efficiency.json", "cad/part.py", "input/input.stl"):
        assert rel in m["hashes"], rel
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    assert validate_document(result, "result").ok
    assert result["gate"]["ok"], result["gate"]
    assert result["pass"] is True
    assert result["scores"]["plan_score"] == pytest.approx(100.0)
    assert result["scores"]["S1"] >= 39.0
    assert result["metrics"]["key_dims_ok"] == pytest.approx(1.0)
    assert result["efficiency"]["turns"] == 8
    assert result["track"] == "T" and result["skillset"]["id"] == "build123d@v1"
