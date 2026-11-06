"""GT-copy backstop (gate): a candidate that IS gt.step fails the gate as 'suspected GT copy'
outside Track T; a scan-noise-level reconstruction does not (synthetic parts, CI)."""

from __future__ import annotations

import json

import pytest
from recadbench_eval import brep, golden, gtstore
from recadbench_eval.config import EvalConfig
from recadbench_eval.gate import GateResult, gt_copy_gate
from recadbench_eval.score import score_run, validate_result

CFG = EvalConfig(n_samples=8000, icp_samples=4000)


@pytest.fixture()
def gt_env(synth_gt_root, monkeypatch):
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    return synth_gt_root


def _good(tmp_path, pid="syn_scan", track="E", scale=None):
    part = gtstore.load_part(pid)
    shape = brep.load_step(part.step_path)
    if scale is not None:
        shape = shape.scale(scale)
    golden.build("good", tmp_path, part_id=pid, gt_shape=shape, gt_plan=part.plan,
                 key_dims=part.key_dims, meta=part.meta, T_scan_to_gt=part.T_scan_to_gt, cfg=CFG)
    m = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    m["track"] = track
    (tmp_path / "run_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return tmp_path


def test_gt_copy_fails_the_gate(gt_env, tmp_path):
    res = score_run(_good(tmp_path), "syn_scan", CFG)
    validate_result(res)
    assert not res["gate"]["ok"]
    assert "suspected GT copy" in res["gate"]["reason"]
    assert res["scores"]["total"] == 0.0 and res["pass"] is False
    gc = res["details"]["gt_copy"]
    assert gc["suspected"] and gc["enforced"] and gc["faces_pred"] == gc["faces_gt"]
    assert gc["volume_rel"] < 1e-6 and gc["p95_all_mm"] < gc["p95_limit_mm"]
    assert res["details"]["gate"]["checks"]["gt_copy"] is False


def test_gt_copy_reported_not_enforced_in_track_t(gt_env, tmp_path):
    """Track T executes the reference plan: GT-identical geometry is the expected result."""
    res = score_run(_good(tmp_path, track="T"), "syn_scan", CFG)
    assert res["gate"]["ok"], res["gate"]
    assert res["details"]["gt_copy"]["suspected"] and not res["details"]["gt_copy"]["enforced"]


def test_gt_copy_check_can_be_switched_off(gt_env, tmp_path):
    res = score_run(_good(tmp_path), "syn_scan", EvalConfig(n_samples=8000, icp_samples=4000,
                                                            gt_copy_check=False))
    assert res["gate"]["ok"]
    assert res["details"]["gt_copy"]["suspected"] and not res["details"]["gt_copy"]["enforced"]


def test_scaled_candidate_is_not_a_copy(gt_env, tmp_path):
    """1 % larger: volume 3 % off, deviation of the order of the bands -> not a copy."""
    res = score_run(_good(tmp_path, scale=1.01), "syn_scan", CFG)
    gc = res["details"]["gt_copy"]
    assert not gc["suspected"] and "gt_copy" not in (res["gate"].get("reason") or "")


@pytest.mark.parametrize("vol,faces,p95,expect", [
    (1000.0, 38, 0.001, True),     # copy
    (1000.5, 38, 0.001, True),     # 5e-4 volume: still a copy
    (1002.0, 38, 0.001, False),    # 2e-3 volume
    (1000.0, 41, 0.001, False),    # 3 faces more (> 5 %)
    (1000.0, 38, 0.05, False),     # P95 above 0.2 x floor (0.039)
    (1000.0, 96, 0.107, False),    # the hs01 oracle replay
])
def test_gt_copy_gate_thresholds(vol, faces, p95, expect):
    g = GateResult()
    out = gt_copy_gate(g, pred_volume=vol, gt_volume=1000.0, faces_pred=faces, faces_gt=38,
                       p95_all=p95, floor_p95=0.195, tight_class=0.1, cfg=EvalConfig())
    assert out["suspected"] is expect and g.ok is (not expect)
    assert out["p95_limit_mm"] == pytest.approx(0.039)


def test_gt_copy_limit_uses_class_band_without_floor():
    g = GateResult()
    out = gt_copy_gate(g, pred_volume=1.0, gt_volume=1.0, faces_pred=6, faces_gt=6,
                       p95_all=0.019, floor_p95=0.0, tight_class=0.1, cfg=EvalConfig())
    assert out["p95_limit_mm"] == pytest.approx(0.02) and out["suspected"]
