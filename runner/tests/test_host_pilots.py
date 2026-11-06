"""Evaluator-host checks on the R385 pilots (skip without RECADBENCH_GT_DIR / the private repos).

* the Track T seed plan of each pilot (gt_plan.json mapped with inv(T_scan_to_gt)) is valid and
  plan-scores exactly like the GT plan in its own frame (the scorer rotates by T_scan_to_gt);
* the H8 dry run: oracle x build123d@v1 x r385_hs01_motor_mount through compose, the replay
  agent, finish and recadbench-eval -> a schema-valid result.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PILOTS = ["r385_hs01_motor_mount", "r385_dr02_wobble_plate", "r385_hs04_pump_head"]
needs_gt = pytest.mark.skipif(not os.environ.get("RECADBENCH_GT_DIR"),
                              reason="host-only: RECADBENCH_GT_DIR not set")


def _gt_has(part: str) -> bool:
    root = os.environ.get("RECADBENCH_GT_DIR")
    return bool(root) and (Path(root) / part / "gt_plan.json").is_file()


@needs_gt
@pytest.mark.parametrize("part", PILOTS)
def test_pilot_seed_plan_scores_like_gt(part):
    if not _gt_has(part):
        pytest.skip(f"{part} not in the GT store")
    from recadbench_eval import gtstore
    from recadbench_eval.config import CLASS_KEYDIM_TOL
    from recadbench_eval.plan_score import plan_score
    from recadbench_run.seed import seed_plan_for

    seed, _ = seed_plan_for(part)
    gp = gtstore.load_part(part)
    tol = CLASS_KEYDIM_TOL[gp.meta["tolerance_class"]]
    ref = plan_score(gp.plan, gp.plan, gp.key_dims, tol, None)
    got = plan_score(seed, gp.plan, gp.key_dims, tol, gp.T_scan_to_gt)
    assert got["valid"] and got["plan_score"] == pytest.approx(ref["plan_score"])
    assert got["datum"]["angle_deg"] == pytest.approx(0.0, abs=1e-4)


@needs_gt
@pytest.mark.skipif(not all(os.environ.get(v) for v in (
    "RECADBENCH_DATA_DIR", "RECADBENCH_SKILLS_DIR", "RECADBENCH_STRATEGIES_DIR")),
    reason="host-only: private repos not configured")
def test_dry_run_oracle_build123d_hs01(tmp_path):
    part = "r385_hs01_motor_mount"
    if not _gt_has(part):
        pytest.skip("pilot not in the GT store")
    from recadbench_run.agents import replay
    from recadbench_run.compose import ComposeRequest, compose
    from recadbench_run.finish import finish
    from recadbench_run.seed import seed_plan_for
    from recadbench_spec import validate_document

    seed, _ = seed_plan_for(part)
    run = compose(ComposeRequest(
        strategy="oracle", skillset="build123d@v1", part_id=part, out=tmp_path / "dry",
        seed_plan=seed, agent_backend="replay", model="replay", provider="replay",
        strategy_ref=os.environ.get("RECADBENCH_STRATEGIES_REF", "origin/main")))
    res = replay.run(run, "oracle_build123d")
    assert res.ok, res.details
    out = finish(run, efficiency=res.efficiency, score=True)
    assert out["ok"] and out["score"]["ok"], out
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    assert validate_document(result, "result").ok
    assert result["gate"]["ok"]
    assert result["scores"]["plan_score"] == pytest.approx(100.0)
