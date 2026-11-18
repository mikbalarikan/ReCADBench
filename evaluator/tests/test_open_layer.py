"""Open layer (ADR 0006, contract section 1): STEP-only scoring on synthetic parts (CI).

G0 is geometry-only (features.json never required, only reported as ``declared``), S2 is S2-face
(``q = faces_pred / faces_gt``), S1 / S3 / S4 equal the official layer, no S5, never PASS, and the
GT-copy backstop is always enforced. The official layer must not change.
"""

from __future__ import annotations

import json

import pytest
from recadbench_eval import brep, golden, gtstore
from recadbench_eval.config import EvalConfig
from recadbench_eval.parsimony import s2_face
from recadbench_eval.score import score_run, validate_result

CFG = EvalConfig(n_samples=8000, icp_samples=4000)
PID = "syn_scan"
SCALE = 1.01  # 1 % larger: not a GT copy (volume 3 % off), still well inside the bbox gate


def _candidate(run_dir, kind="good", *, scale=SCALE, features=True, track="E"):
    """A golden candidate (scanner frame); ``features=False`` drops features.json (STEP only)."""
    part = gtstore.load_part(PID)
    shape = brep.load_step(part.step_path)
    if scale is not None:
        shape = shape.scale(scale)
    golden.build(kind, run_dir, part_id=PID, gt_shape=shape, gt_plan=part.plan,
                 key_dims=part.key_dims, meta=part.meta, T_scan_to_gt=part.T_scan_to_gt, cfg=CFG)
    if not features:
        (run_dir / "out" / "features.json").unlink()
    m = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    m["track"] = track
    (run_dir / "run_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return run_dir


# ------------------------------------------------------------------------------- S2-face unit
@pytest.mark.parametrize("fp,fg,score,cap", [
    (38, 38, 25.0, False),       # q = 1
    (14, 40, 25.0, False),       # q = 0.35: plateau starts
    (60, 40, 25.0, False),       # q = 1.5: plateau ends
    (17, 10, 15.0, False),       # q = 1.7
    (20, 10, 15.0, False),       # q = 2.0: still the 15 step
    (21, 10, 5.0, False),        # q = 2.1
    (3, 10, 10.0, False),        # q = 0.3: low step
    (2, 10, 0.0, False),         # q = 0.2
    (30, 10, 5.0, False),        # q = 3.0: last step, not capped yet
    (31, 10, 0.0, True),         # q = 3.1: capped
    (5, 0, 0.0, False),          # no GT faces: q = 0
])
def test_s2_face_curve(fp, fg, score, cap):
    r = s2_face(fp, fg)
    assert r["S2"] == pytest.approx(score)
    assert r["cap_total"] is cap and r["provisional"] is True
    assert r["face_ratio"] == pytest.approx(fp / fg if fg else 0.0)


# ------------------------------------------------------------------------------- score_run
@pytest.fixture()
def pair(gt_env, tmp_path):
    """The same candidate scored in both layers (features.json from the plan)."""
    d = _candidate(tmp_path / "run")
    return score_run(d, PID, CFG), score_run(d, PID, CFG, layer="open")


def test_open_step_only(gt_env, tmp_path):
    res = score_run(_candidate(tmp_path, features=False), PID, CFG, layer="open")
    validate_result(res)
    assert res["gate"]["ok"], res["gate"]
    assert res["track"] == "E" and res["details"]["layer"] == "open"
    assert "declared" not in res["details"]
    assert "features" not in res["details"]["gate"]["checks"]
    assert "unmapped" not in res["details"]["gate"]["checks"]
    assert res["scores"]["S2"] == 25.0 and res["scores"]["S5"] is None
    assert res["metrics"]["face_ratio"] == 1.0
    assert "r_feat" not in res["metrics"] and "param_coverage" not in res["metrics"]
    assert res["details"]["S2"]["provisional"] is True
    assert res["pass"] is False and "pass_checks" not in res["details"]
    pc = res["details"]["pass_checks_open"]
    assert set(pc) == {"gate", "s1_targets", "q_in_range", "key_dims"}
    assert pc["gate"] is True and pc["q_in_range"] is True
    assert res["details"]["gt_copy"]["enforced"] is True


def test_official_without_features_still_fails(gt_env, tmp_path):
    res = score_run(_candidate(tmp_path, features=False), PID, CFG)
    assert not res["gate"]["ok"] and "features.json missing" in res["gate"]["reason"]
    assert "layer" not in res["details"]  # official output unchanged


def test_s1_s3_s4_match_official(pair):
    off, opn = pair
    assert off["gate"]["ok"] and opn["gate"]["ok"]
    for k in ("S1", "S3", "S4"):
        assert opn["scores"][k] == off["scores"][k], k
    for k in ("p_tight", "p_wide", "p95", "key_dims_ok", "b1_match", "face_type_tvd"):
        assert opn["metrics"][k] == off["metrics"][k], k
    assert opn["details"]["S3"] == off["details"]["S3"]
    assert opn["scores"]["total"] == pytest.approx(
        opn["scores"]["S1"] + opn["scores"]["S2"] + opn["scores"]["S3"] + opn["scores"]["S4"])
    assert opn["metrics"]["face_ratio"] == off["metrics"]["face_ratio"]
    assert "layer" not in off["details"] and "pass_checks_open" not in off["details"]


def test_declared_features_never_change_the_score(gt_env, tmp_path):
    """lego features (16 counted boxes): official S2 = 0 and capped; Open ignores them."""
    base = score_run(_candidate(tmp_path / "a", features=False), PID, CFG, layer="open")
    d = _candidate(tmp_path / "b", "lego")
    off = score_run(d, PID, CFG)
    opn = score_run(d, PID, CFG, layer="open")
    assert off["scores"]["S2"] == 0.0 and off["metrics"]["r_feat"] == 4.0
    assert off["scores"]["total"] <= 40.0  # official cap applies (r > 3)
    assert opn["details"]["declared"] == {"n_counted": 16, "unmapped": 0}
    assert opn["scores"] == base["scores"]
    assert opn["scores"]["S2"] == 25.0 and opn["scores"]["total"] > 40.0


def test_unmapped_is_declared_not_a_gate_failure(gt_env, tmp_path):
    d = _candidate(tmp_path, "dumb_solid")
    assert "unmapped" in score_run(d, PID, CFG)["gate"]["reason"]
    opn = score_run(d, PID, CFG, layer="open")
    assert opn["gate"]["ok"], opn["gate"]
    assert opn["details"]["declared"] == {"n_counted": 0, "unmapped": 1}


@pytest.mark.parametrize("payload", [b"{not json", b"[1, 2]", "ÿ".encode("latin-1")])
def test_unreadable_features_are_ignored(gt_env, tmp_path, payload):
    d = _candidate(tmp_path, features=False)
    (d / "out" / "features.json").write_bytes(payload)
    res = score_run(d, PID, CFG, layer="open")
    assert res["gate"]["ok"], res["gate"]
    assert "declared" not in res["details"]


def test_gt_copy_always_enforced(gt_env, tmp_path):
    """Even with the backstop switched off in cfg and a Track T manifest."""
    d = _candidate(tmp_path, scale=None, features=False, track="T")
    cfg = EvalConfig(n_samples=8000, icp_samples=4000, gt_copy_check=False)
    res = score_run(d, PID, cfg, layer="open")
    assert not res["gate"]["ok"] and "suspected GT copy" in res["gate"]["reason"]
    assert res["details"]["gt_copy"]["enforced"] is True
    assert res["scores"]["total"] == 0.0 and res["pass"] is False
    assert res["details"]["layer"] == "open"
    validate_result(res)


def test_faceted_fails_open_gate(gt_env, tmp_path):
    d = _candidate(tmp_path, "faceted", features=False)
    res = score_run(d, PID, CFG, layer="open")
    assert not res["gate"]["ok"] and "faceted" in res["gate"]["reason"]
    assert res["scores"]["S2"] == 0.0


def test_s5_never_in_open(gt_env, tmp_path):
    d = _candidate(tmp_path, features=False)
    cfg = EvalConfig(n_samples=8000, icp_samples=4000, s5=True)
    res = score_run(d, PID, cfg, layer="open")
    assert res["scores"]["S5"] is None and "S5" not in res["details"]
    assert res["details"]["config"]["s5"] is False


def test_unknown_layer(gt_env, tmp_path):
    with pytest.raises(ValueError, match="unknown layer"):
        score_run(tmp_path, PID, CFG, layer="verified")


# ------------------------------------------------------------------------------- untrusted inputs
DEEP = b"[" * 200_000 + b"]" * 200_000  # RecursionError in the json decoder


def test_deeply_nested_features_are_ignored(gt_env, tmp_path):
    d = _candidate(tmp_path, features=False)
    (d / "out" / "features.json").write_bytes(DEEP)
    res = score_run(d, PID, CFG, layer="open")
    assert res["gate"]["ok"], res["gate"]
    assert "declared" not in res["details"]


def test_features_at_run_dir_root_are_declared(gt_env, tmp_path):
    d = _candidate(tmp_path, "lego")
    (d / "out" / "features.json").replace(d / "features.json")
    res = score_run(d, PID, CFG, layer="open")
    assert res["gate"]["ok"], res["gate"]
    assert res["details"]["declared"] == {"n_counted": 16, "unmapped": 0}


def _plans(d, *payloads):
    (d / "plan").mkdir(exist_ok=True)
    for i, p in enumerate(payloads):
        (d / "plan" / f"re_plan.v{i}.json").write_bytes(p)


@pytest.mark.parametrize("payloads", [
    (b"{}", b"{not json"),                         # malformed final plan
    (b"{}", b"[1, 2]"),                            # final plan not an object
    (b"{}", DEEP),                                 # final plan nested too deep
    (b"{}", "\xff".encode("latin-1")),             # final plan not UTF-8
])
def test_bad_final_plan_never_aborts_geometry(gt_env, tmp_path, payloads):
    base = score_run(_candidate(tmp_path / "a", features=False), PID, CFG, layer="open")
    d = _candidate(tmp_path / "b", features=False)
    _plans(d, *payloads)
    res = score_run(d, PID, CFG, layer="open")
    validate_result(res)
    assert res["gate"]["ok"], res["gate"]
    assert res["details"]["plan"]["valid"] is False
    assert res["scores"]["plan_score"] == 0.0
    assert "plan_drift" not in res["metrics"]
    for k in ("S1", "S2", "S3", "S4", "total"):
        assert res["scores"][k] == base["scores"][k], k


@pytest.mark.parametrize("first", [b"[1, 2]", b'{"features": [1, "a"]}', b"{not json", DEEP])
def test_bad_first_plan_never_aborts_geometry(gt_env, tmp_path, first):
    part = gtstore.load_part(PID)
    d = _candidate(tmp_path, features=False)
    _plans(d, first, json.dumps(part.plan).encode("utf-8"))
    res = score_run(d, PID, CFG, layer="open")
    validate_result(res)
    assert res["gate"]["ok"], res["gate"]
    assert isinstance(res["scores"]["plan_score"], float)
    assert "plan_drift" not in res["metrics"]
    if res["details"]["plan"].get("valid", True) is not False:
        assert "unreadable" in res["details"]["plan"]["drift_error"]


def test_valid_plans_are_scored_in_open(gt_env, tmp_path):
    part = gtstore.load_part(PID)
    plan = json.dumps(part.plan).encode("utf-8")
    d = _candidate(tmp_path, features=False)
    _plans(d, plan, plan)
    opn = score_run(d, PID, CFG, layer="open")
    assert opn["gate"]["ok"], opn["gate"]
    assert opn["details"]["plan"]["file"] == "re_plan.v1.json"
    assert isinstance(opn["scores"]["plan_score"], float)
    assert opn["details"]["plan"].get("valid", True) is not False  # GT plan is a valid IR
    assert opn["metrics"]["plan_drift"] == 0.0
    # same files as the official layer would see (features.json added back for it)
    d2 = _candidate(tmp_path / "off")
    _plans(d2, plan, plan)
    off = score_run(d2, PID, CFG)
    assert opn["scores"]["plan_score"] == off["scores"]["plan_score"]
    assert opn["metrics"].get("plan_drift") == off["metrics"].get("plan_drift")


def test_track_s_manifest_still_scores_geometry_in_open(gt_env, tmp_path):
    d = _candidate(tmp_path, features=False, track="S")
    res = score_run(d, PID, CFG, layer="open")
    validate_result(res)
    assert res["gate"]["ok"], res["gate"]
    assert res["scores"]["total"] > 0.0
    assert res["details"]["layer"] == "open"
    off = score_run(d, PID, CFG)
    assert "plan-only run" in off["gate"]["reason"]  # official layer unchanged
