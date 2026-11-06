"""Regression tests for the H5 review findings (each fails on the pre-fix evaluator).

Synthetic parts only (generated in-test by conftest.py); candidates are built in tmp dirs and
written like real predictions (scanner frame, out/features.json).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from recadbench_eval import brep, golden, gtstore
from recadbench_eval.config import EvalConfig
from recadbench_eval.intent import DimResult, score_s3
from recadbench_eval.parsimony import s2
from recadbench_eval.score import score_run, validate_result

_spec = importlib.util.spec_from_file_location("_syn_conftest",
                                               Path(__file__).with_name("conftest.py"))
_syn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_syn)
HOLES, build_shape = _syn.HOLES, _syn.build_shape

CFG = EvalConfig(n_samples=20000, icp_samples=8000)


def _run(tmp_path, name, shape_gt, part, feats=None, raw: bytes | None = None):
    d = tmp_path / name
    out = d / "out"
    out.mkdir(parents=True)
    brep.write_step(brep.transform(shape_gt, np.linalg.inv(part.T_scan_to_gt)),
                    out / "output.step")
    if raw is not None:
        (out / "features.json").write_bytes(raw)
    elif feats is not None:
        (out / "features.json").write_text(json.dumps(feats), encoding="utf-8")
    return d


def _good_feats(part, shape):
    return golden.features_from_plan(part.plan, len(shape.faces()))


def _dim(res, name):
    return next(d for d in res["details"]["S3"]["dims"] if d["name"] == name)


# --------------------------------------------------- gate: scale check no longer vertex-PCA
@pytest.mark.parametrize("pid", ["syn_clean", "syn_scan"])
def test_missing_small_hole_passes_gate_and_loses_its_dim(gt_env, tmp_path, pid):
    """One of the two D4.2 holes missing: the old vertex-PCA extents check zeroed it as
    'units?'; the old face lookup credited hole_d from the other hole 14 mm away."""
    part = gtstore.load_part(pid)
    cand = build_shape(holes=[HOLES[1]])
    res = score_run(_run(tmp_path, "miss", cand, part, _good_feats(part, cand)), pid, CFG)
    assert res["gate"]["ok"], res["gate"]
    assert res["scores"]["total"] > 50
    hd = _dim(res, "hole_d")
    assert hd["ok"] is False and hd["measured"] is None
    assert "near near_point" in hd["note"]
    assert _dim(res, "hole_pitch")["ok"] is False
    assert res["metrics"]["key_dims_ok"] == pytest.approx(5 / 7)


# ------------------------------------------------------------------------------ gate: b2
@pytest.mark.parametrize("void", [
    ((0.0, -8.0, 3.0), (3.0, 3.0, 2.0)),  # small internal void
    ((-1.5, 0.0, 3.15), (16.0, 29.4, 5.7)),  # hollow: thin walls around a big cavity
])
def test_internal_void_fails_gate(gt_env, tmp_path, void):
    from build123d import Box, Pos

    part = gtstore.load_part("syn_clean")
    gt = brep.load_step(part.step_path)
    (x, y, z), (a, b, c) = void
    cand = (gt - Pos(x, y, z) * Box(a, b, c)).solids()[0]
    assert len(cand.shells()) == 2
    res = score_run(_run(tmp_path, "void", cand, part, _good_feats(part, cand)), "syn_clean",
                    CFG)
    assert not res["gate"]["ok"]
    assert "internal void (b2 != 0)" in res["gate"]["reason"]
    assert res["scores"]["total"] == 0.0 and res["pass"] is False


def test_volume_sanity_check(gt_env, tmp_path):
    """A solid with the GT's outer surface but a fraction of its volume fails the gate even
    without the b2 check (e.g. a skin plus an inward offset counted as one shell by a bad
    exporter): checked through static_gate directly."""
    from recadbench_eval.gate import GateResult, static_gate

    g = static_gate(GateResult(), n_solids=1, valid=True, closed=True, recs=None,
                    n_triangles_planar=0, faces_gt=11, pred_volume=0.23 * 1000.0,
                    gt_volume=1000.0, features={"unmapped": []}, cfg=CFG)
    assert not g.ok and "volume" in g.reason and "units?" in g.reason
    g = static_gate(GateResult(), n_solids=1, valid=True, closed=True, recs=None,
                    n_triangles_planar=0, faces_gt=11, pred_volume=0.98 * 1000.0,
                    gt_volume=1000.0, features={"unmapped": []}, cfg=CFG)
    assert g.ok


# ------------------------------------------------------------------- features.json reading
def test_features_with_bom_and_unreadable(gt_env, tmp_path):
    part = gtstore.load_part("syn_clean")
    gt = brep.load_step(part.step_path)
    feats = _good_feats(part, gt)
    raw = json.dumps(feats).encode("utf-8")
    res = score_run(_run(tmp_path, "bom", gt, part, raw=b"\xef\xbb\xbf" + raw), "syn_clean", CFG)
    assert res["gate"]["ok"] and res["pass"] is True
    res = score_run(_run(tmp_path, "junk", gt, part, raw=b"{not json"), "syn_clean", CFG)
    assert not res["gate"]["ok"] and "features.json unreadable" in res["gate"]["reason"]
    validate_result(res)


def test_inconsistent_count_fails_gate(gt_env, tmp_path):
    """LEGO list with ``count`` forged to n_ref: the spec validator rejects it, so does the gate
    (before: S2 12.5, total 82.5, PASS)."""
    part = gtstore.load_part("syn_clean")
    gt = brep.load_step(part.step_path)
    n_ref = int(part.meta["n_ref"])
    feats = golden.lego_features(4 * n_ref, len(gt.faces()))
    feats["count"] = n_ref
    res = score_run(_run(tmp_path, "fake", gt, part, feats), "syn_clean", CFG)
    assert not res["gate"]["ok"] and res["pass"] is False
    assert "features.json invalid" in res["gate"]["reason"] and "count" in res["gate"]["reason"]


def test_s2_counts_rows_not_the_count_field():
    feats = {"count": 1, "features": [{"counted": True}] * 6 + [{"counted": False}] * 3,
             "parameters": []}
    r = s2(feats, 4, 11, 11, [], 0.02, CFG)
    assert r["n_pred"] == 6 and r["r_feat"] == 1.5


# -------------------------------------------------------------- S1: pred side never masked
def test_material_over_unobserved_patch_is_scored(gt_env, tmp_path):
    """syn_scan: the bottom face is unobserved. A boss glued under it used to be dropped from
    S1 entirely (its pred samples' feet lie on unobserved GT)."""
    from build123d import Cylinder, Pos

    part = gtstore.load_part("syn_scan")
    gt = brep.load_step(part.step_path)
    cand = (gt + Pos(0.0, -6.0, -1.0) * Cylinder(4.5, 2.0)).solids()[0]
    res = score_run(_run(tmp_path, "boss", cand, part, _good_feats(part, cand)), "syn_scan", CFG)
    assert res["gate"]["ok"], res["gate"]
    assert res["metrics"]["p_tight"] < 0.995
    assert res["metrics"]["p_wide"] < 0.99
    assert res["pass"] is False
    assert res["details"]["pass_checks"]["s1_targets"] is False


# --------------------------------------------------------------- scoring-rule known answers
def test_s1_below_target_fails_pass(gt_env, tmp_path):
    """All key dims right and r = 1, but the +y side (not a key dim) moved out 0.5 mm."""
    part = gtstore.load_part("syn_clean")
    gt = brep.load_step(part.step_path)
    side = [r for r in brep.face_records(gt) if r.kind == "plane"
            and np.allclose(r.normal, [0, 1, 0], atol=1e-9)]
    assert len(side) == 1
    cand = golden.move_faces(gt, side, 0.5)
    res = score_run(_run(tmp_path, "skin", cand, part, _good_feats(part, cand)), "syn_clean",
                    CFG)
    assert res["gate"]["ok"]
    assert res["metrics"]["key_dims_ok"] == 1.0 and res["metrics"]["r_feat"] == 1.0
    assert res["details"]["pass_checks"]["s1_targets"] is False
    assert res["scores"]["S1"] < 40.0
    assert res["pass"] is False


def test_broken_coaxial_group_costs_relations(gt_env, tmp_path):
    """Bore 0.1 mm off the boss axis: the GT coaxial group (boss, bore) is broken."""
    part = gtstore.load_part("syn_clean")
    cand = build_shape(bore_c=(10.1, 0.0))
    res = score_run(_run(tmp_path, "coax", cand, part, _good_feats(part, cand)), "syn_clean",
                    CFG)
    rels = res["details"]["S3"]["relations_detail"]
    coax = [r for r in rels if r["kind"] == "coaxial"]
    assert coax and coax[0]["ok"] is False
    assert res["details"]["S3"]["relations_ok"] < 1.0
    assert res["scores"]["S3"] < 20.0


def test_param_coverage_halves_s2(gt_env, tmp_path):
    part = gtstore.load_part("syn_clean")
    gt = brep.load_step(part.step_path)
    feats = _good_feats(part, gt)
    feats["parameters"] = []
    for f in feats["features"]:
        f["params"] = {}
    res = score_run(_run(tmp_path, "nopar", gt, part, feats), "syn_clean", CFG)
    assert res["metrics"]["param_coverage"] < 0.5
    assert res["details"]["S2"]["halved_by"] == ["param_coverage"]
    assert res["scores"]["S2"] == 12.5


def test_face_ratio_halves_s2():
    feats = {"features": [{"counted": True}] * 4, "parameters": []}
    r = s2(feats, 4, 25, 11, [], 0.02, CFG)  # 25 / 11 = 2.27 > 2
    assert r["halved_by"] == ["face_ratio"] and r["S2"] == 12.5
    r = s2(feats, 4, 22, 11, [], 0.02, CFG)  # exactly 2: not halved
    assert r["halved_by"] == [] and r["S2"] == 25.0


def test_standard_credit_follows_key_dims_when_gt_has_no_standard_dims():
    dims = [DimResult("a", "diameter", 1.0, 1.0, 0.0, True, False),
            DimResult("b", "diameter", 2.0, 2.5, 0.5, False, False)]
    s3, d = score_s3(dims, [])
    assert d["standard_dims"] == 0 and d["standard_ok"] == 0.5 and d["relations_ok"] == 0.5
    assert s3 == pytest.approx(10.0)
    dims_std = [DimResult("a", "diameter", 1.0, 1.0, 0.0, True, True), dims[1]]
    s3, d = score_s3(dims_std, [{"ok": False}])
    assert d["standard_ok"] == 1.0 and d["relations_ok"] == 0.0
    assert s3 == pytest.approx(6.0 + 4.0)
