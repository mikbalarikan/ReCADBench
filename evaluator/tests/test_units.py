"""Unit tests with known ground truth (synthetic, CI)."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from recadbench_eval import brep, golden, gtstore
from recadbench_eval.config import EvalConfig, lin
from recadbench_eval.geom import Surface, _closest_on_triangles, sample
from recadbench_eval.parsimony import ratio_curve
from recadbench_eval.plan_score import plan_score
from recadbench_eval.topology import tvd

CFG = EvalConfig(n_samples=8000, icp_samples=4000)


# ------------------------------------------------------------------------------ geometry
def test_closest_point_matches_brute_force():
    rng = np.random.default_rng(3)
    tri = rng.normal(size=(500, 3, 3))
    p = rng.normal(size=(500, 3)) * 2
    got = _closest_on_triangles(tri, p)
    # brute force on a dense barycentric grid
    u, v = np.meshgrid(np.linspace(0, 1, 120), np.linspace(0, 1, 120))
    m = (u + v) <= 1
    u, v = u[m], v[m]
    for i in range(0, 500, 50):
        a, b, c = tri[i]
        pts = a + u[:, None] * (b - a) + v[:, None] * (c - a)
        d_brute = np.linalg.norm(pts - p[i], axis=1).min()
        assert np.linalg.norm(got[i] - p[i]) <= d_brute + 1e-9
        assert np.linalg.norm(got[i] - p[i]) >= d_brute - 0.02


def test_surface_distance_on_a_box():
    from build123d import Box

    t = brep.tessellate(Box(10, 10, 10), 0.01)
    s = Surface(t)
    pts = np.array([[0, 0, 7.0], [0, 0, 0], [6, 6, 0], [0, 4.5, 0], [8, 8, 8]])
    d = s.query(pts)[0]
    assert d == pytest.approx([2.0, 5.0, np.sqrt(2.0), 0.5, np.sqrt(27.0)], abs=1e-9)


def test_sample_is_seeded_and_area_uniform():
    from build123d import Box

    t = brep.tessellate(Box(10, 20, 1), 0.01)
    a, b = sample(t, 5000, 7), sample(t, 5000, 7)
    assert np.array_equal(a.points, b.points)
    top = np.mean(a.points[:, 2] > 0.49)
    assert top == pytest.approx(200 / (2 * 200 + 2 * 10 + 2 * 20), abs=0.02)


def test_genus_and_face_records():
    from build123d import Box, Cylinder, Pos, Torus

    plate = Box(20, 20, 2) - Pos(-5, 0, 0) * Cylinder(2, 2) - Pos(5, 0, 0) * Cylinder(2, 2)
    assert brep.genus(plate.solids()[0]) == 2
    assert brep.genus(Torus(10, 2).solids()[0]) == 1
    recs = brep.face_records(Box(2, 2, 2).solids()[0])
    assert {r.kind for r in recs} == {"plane"} and len(recs) == 6
    for r in recs:  # outward normals point away from the centre
        assert float(np.dot(r.normal, r.origin)) > 0 or np.linalg.norm(r.origin) == 0
        c = r.face.center()
        assert float(np.dot(r.normal, [c.X, c.Y, c.Z])) == pytest.approx(1.0)


# --------------------------------------------------------------------------------- curves
def test_curves():
    assert lin(0.6, 0.6, 0.95) == 0 and lin(0.95, 0.6, 0.95) == 1
    assert ratio_curve(1.0) == 25 and ratio_curve(1.2) == 25 and ratio_curve(3.0) == 0
    assert ratio_curve(2.1) == pytest.approx(12.5)
    assert ratio_curve(0.6) == pytest.approx(17.5) and ratio_curve(0.49) == 0
    assert ratio_curve(1.0, 15) == 15
    assert tvd({"plane": 1.0}, {"plane": 0.5, "cylinder": 0.5}) == pytest.approx(0.5)


# ----------------------------------------------------------------------------- plan score
def test_plan_score_self_and_scanner_frame(gt_env):
    part = gtstore.load_part("syn_scan")
    ps = plan_score(part.plan, part.plan, part.key_dims, 0.02, part.T_scan_to_gt)
    # the GT plan is in the GT frame; scored as if it were a scanner-frame IR the datum fails
    assert ps["components"]["datums"] == 5.0
    ir = copy.deepcopy(part.plan)
    Rinv = np.linalg.inv(part.T_scan_to_gt)[:3, :3]
    for d in ir["datums"]:
        d["direction"] = (Rinv @ np.asarray(d["direction"], float)).tolist()
    ps = plan_score(ir, part.plan, part.key_dims, 0.02, part.T_scan_to_gt)
    assert ps["plan_score"] == pytest.approx(100.0)
    clean = gtstore.load_part("syn_clean")
    assert plan_score(clean.plan, clean.plan, clean.key_dims, 0.02)["plan_score"] == 100.0


def test_plan_score_penalises(gt_env):
    part = gtstore.load_part("syn_clean")
    ir = copy.deepcopy(part.plan)
    for p in ir["parameters"]:
        if p["name"] == "hole_d":
            p["value"] = 5.0
    ir["features"].append({"id": "EXTRA", "type": "fillet",
                           "params": {"radius": 1.0, "edges": "PLATE:edges:convex"}})
    ps = plan_score(ir, part.plan, part.key_dims, 0.02)
    assert ps["key_dims_missing"] == ["hole_d"]
    assert 60 < ps["plan_score"] < 100
    bad = {"spec_version": "0.3.0"}
    assert plan_score(bad, part.plan, part.key_dims, 0.02)["plan_score"] == 0.0


# ---------------------------------------------------------------------------------- gate
def _run_dir(tmp_path, shape_gt, feats, part):
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    brep.write_step(brep.transform(shape_gt, np.linalg.inv(part.T_scan_to_gt)),
                    out / "output.step")
    if feats is not None:
        (out / "features.json").write_text(json.dumps(feats), encoding="utf-8")
    return tmp_path


def test_gate_units_freeform_missing_features(gt_env, tmp_path):
    from build123d import Shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from recadbench_eval.score import score_run

    part = gtstore.load_part("syn_clean")
    gt = brep.load_step(part.step_path)
    feats = golden.features_from_plan(part.plan, len(gt.faces()))
    inch = gt.scale(25.4)
    res = score_run(_run_dir(tmp_path / "inch", inch, feats, part), "syn_clean", CFG)
    assert not res["gate"]["ok"] and "units" in res["gate"]["reason"]
    nurbs = Shape.cast(BRepBuilderAPI_NurbsConvert(gt.wrapped, True).Shape())
    res = score_run(_run_dir(tmp_path / "nurbs", nurbs, feats, part), "syn_clean", CFG)
    assert not res["gate"]["ok"] and "freeform" in res["gate"]["reason"]
    res = score_run(_run_dir(tmp_path / "nofeat", gt, None, part), "syn_clean", CFG)
    assert not res["gate"]["ok"] and "features.json missing" in res["gate"]["reason"]
    from build123d import Box, Pos

    two = (gt + Pos(100, 0, 0) * Box(5, 5, 5))
    res = score_run(_run_dir(tmp_path / "two", two, feats, part), "syn_clean", CFG)
    assert not res["gate"]["ok"] and "2 solids" in res["gate"]["reason"]
    res = score_run(tmp_path / "empty", "syn_clean", CFG)
    assert not res["gate"]["ok"] and "not found" in res["gate"]["reason"]


# ------------------------------------------------------------------------------------- S5
def test_s5_behind_flag(gt_env, tmp_path):
    from recadbench_eval.score import score_run

    part = gtstore.load_part("syn_scan")
    gt = brep.load_step(part.step_path)
    d = tmp_path / "run"
    golden.build("good", d, part_id="syn_scan", gt_shape=gt, gt_plan=part.plan,
                 key_dims=part.key_dims, meta=part.meta, T_scan_to_gt=part.T_scan_to_gt)
    bore = next(x for x in part.key_dims["dims"] if x["name"] == "bore_d")
    edited, got = golden.set_dim(gt, bore, 6.6, CFG)
    assert got == pytest.approx(6.6)
    s5dir = d / "out" / "s5"
    s5dir.mkdir()
    brep.write_step(brep.transform(edited, np.linalg.inv(part.T_scan_to_gt)),
                    s5dir / "bore.step")
    edits = {"edits": [
        {"key_dim": "bore_d", "param": "bore_d", "old": 6.2, "new": 6.6,
         "step": "s5/bore.step", "ok": True},
        # claims the bore shrank: volume moved the other way => direction check fails
        {"key_dim": "bore_d", "param": "bore_d", "old": 6.2, "new": 5.8,
         "step": "s5/bore.step", "ok": True},
        {"key_dim": "plate_t", "param": "plate_t", "old": 6.3, "new": 7.0, "ok": False},
    ]}
    (s5dir / "edits.json").write_text(json.dumps(edits), encoding="utf-8")
    off = score_run(d, "syn_scan", CFG)
    assert off["scores"]["S5"] is None
    on = score_run(d, "syn_scan", EvalConfig.with_overrides(n_samples=8000, icp_samples=4000,
                                                             s5=True))
    rows = on["details"]["S5"]["edits"]
    assert [r["ok"] for r in rows] == [True, False, False]
    assert on["scores"]["S5"] == pytest.approx(5 / 3)
    assert on["scores"]["total"] == pytest.approx(off["scores"]["total"] + 5 / 3, abs=1e-5)


# ------------------------------------------------------------------------------ CLI + misc
def test_cli_score_report_plan(gt_env, tmp_path, capsys):
    from recadbench_eval.cli import main

    part = gtstore.load_part("syn_clean")
    gt = brep.load_step(part.step_path)
    d = tmp_path / "run"
    golden.build("lego", d, part_id="syn_clean", gt_shape=gt, gt_plan=part.plan,
                 key_dims=part.key_dims, meta=part.meta, T_scan_to_gt=part.T_scan_to_gt)
    (d / "plan").mkdir()
    (d / "plan" / "re_plan.v1.json").write_text(json.dumps(part.plan), encoding="utf-8")
    cfgf = tmp_path / "eval.yaml"
    cfgf.write_text("n_samples: 6000\nicp_samples: 3000\n", encoding="utf-8")
    assert main(["score", "--run-dir", str(d), "--part", "syn_clean", "--config", str(cfgf)]) == 0
    res = json.loads((d / "result.json").read_text(encoding="utf-8"))
    assert res["scores"]["total"] == 40.0 and res["scores"]["plan_score"] == 100.0
    assert res["metrics"]["ir_adherence"] == 0.0
    md, csvf = tmp_path / "r.md", tmp_path / "r.csv"
    assert main(["report", str(tmp_path), "--md", str(md), "--csv", str(csvf)]) == 0
    assert "| syn_clean |" in md.read_text(encoding="utf-8")
    assert csvf.read_text(encoding="utf-8").startswith("part,run,gate,S1")
    assert main(["plan-score", "--plan", str(d / "plan" / "re_plan.v1.json"),
                 "--part", "syn_clean"]) == 0
    assert '"plan_score": 100.0' in capsys.readouterr().out
    # Track S: plan only
    assert main(["score", "--run-dir", str(d), "--part", "syn_clean", "--track", "S",
                 "--out", str(tmp_path / "s.json")]) == 0
    s = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert s["pass"] is False and s["scores"]["plan_score"] == 100.0 and "S1" not in s["scores"]


def test_gt_only_via_env(monkeypatch):
    monkeypatch.delenv("RECADBENCH_GT_DIR", raising=False)
    assert not gtstore.available()
    with pytest.raises(gtstore.GtUnavailable):
        gtstore.load_part("anything")


def test_no_backend_imports():
    """The evaluator never imports backend / adapter code (CLAUDE.md hard rule)."""
    import pathlib
    import re

    pkg = pathlib.Path(__file__).resolve().parents[1] / "recadbench_eval"
    pat = re.compile(r"^\s*(from|import)\s+(recad_cli|backends|adapter|retk)\b", re.M)
    for f in pkg.glob("*.py"):
        assert not pat.search(f.read_text(encoding="utf-8")), f.name
