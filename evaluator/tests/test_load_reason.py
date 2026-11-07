"""A missing out/output.step reports the finish refusal behind it (P11 pilot: three runs were
reported as 'file not found' when finish had refused a 2-solid or inconsistent STEP)."""

from __future__ import annotations

import json

import pytest
from recadbench_eval.config import EvalConfig
from recadbench_eval.score import _load_reason, score_run, validate_result

CFG = EvalConfig(n_samples=8000, icp_samples=4000)


@pytest.fixture()
def gt_env(synth_gt_root, monkeypatch):
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    return synth_gt_root


def _run(tmp_path, finish):
    (tmp_path / "out").mkdir()
    m = {"run_id": "r0", "part_id": "syn_scan", "track": "E", "finish": finish}
    (tmp_path / "run_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("finish", "expect"),
    [
        ({"export": {"ok": False, "error": "expected exactly one solid, got 2"}},
         "finish export failed: expected exactly one solid, got 2"),
        ({"error": "not extracted: gate state MAC mismatch"},
         "finish failed: not extracted: gate state MAC mismatch"),
        ({"export": {"ok": True}}, "file not found"),
    ],
)
def test_missing_step_reports_finish_reason(gt_env, tmp_path, finish, expect):
    res = score_run(_run(tmp_path, finish), "syn_scan", CFG)
    validate_result(res)
    assert not res["gate"]["ok"] and res["scores"]["total"] == 0.0
    assert expect in res["gate"]["reason"]
    assert "file not found" in res["gate"]["reason"]  # the load error itself stays visible


def test_load_reason_without_manifest():
    assert _load_reason(ValueError("x: file not found"), None) == "x: file not found"
