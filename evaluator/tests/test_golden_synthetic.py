"""Golden tests on synthetic parts generated in-test (public / CI). 03_SCORING + ADR 0002.

Both variants (clean synthetic, and scan-rule synthetic with a known T_scan_to_gt, scan floor and
observability mask) x the six golden candidates; asserts the ordering and PASS flags.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
from recadbench_eval import brep, golden, gtstore
from recadbench_eval.config import EvalConfig
from recadbench_eval.score import dumps, score_run, validate_result, write_result

CFG = EvalConfig(n_samples=20000, icp_samples=8000)
VARIANTS = {"syn_clean": "clean", "syn_scan": "scan"}


@pytest.fixture(scope="module")
def scored(synth_gt_root, tmp_path_factory):
    old = os.environ.get("RECADBENCH_GT_DIR")
    os.environ["RECADBENCH_GT_DIR"] = str(synth_gt_root)
    try:
        out = {}
        for pid in VARIANTS:
            part = gtstore.load_part(pid)
            shape = brep.load_step(part.step_path)
            for kind in golden.KINDS:
                d = tmp_path_factory.mktemp(f"{pid}_{kind}")
                info = golden.build(kind, d, part_id=pid, gt_shape=shape, gt_plan=part.plan,
                                    key_dims=part.key_dims, meta=part.meta,
                                    T_scan_to_gt=part.T_scan_to_gt, cfg=CFG)
                res = score_run(d, pid, CFG)
                write_result(res, d / "result.json")
                out[(pid, kind)] = (res, info, d)
        yield out
    finally:
        if old is None:
            os.environ.pop("RECADBENCH_GT_DIR", None)
        else:
            os.environ["RECADBENCH_GT_DIR"] = old


@pytest.mark.parametrize("pid", list(VARIANTS))
def test_good_passes(scored, pid):
    res, _, _ = scored[(pid, "good")]
    assert res["gate"]["ok"], res["gate"]
    assert res["pass"] is True
    assert res["scores"]["S1"] == pytest.approx(40.0, abs=0.01)
    assert res["scores"]["S2"] == 25.0
    assert res["scores"]["S3"] == pytest.approx(20.0)
    assert res["scores"]["S4"] == pytest.approx(10.0, abs=0.01)
    assert res["metrics"]["key_dims_ok"] == 1.0
    assert res["alignment"]["datum_offset_mm"] < 1e-3


@pytest.mark.parametrize("pid", list(VARIANTS))
def test_ordering_and_pass_flags(scored, pid):
    tot = {k: scored[(pid, k)][0]["scores"]["total"] for k in golden.KINDS}
    ok = {k: scored[(pid, k)][0]["pass"] for k in golden.KINDS}
    assert tot["good"] > tot["wrong_dim"] > tot["lego"]
    assert tot["good"] > tot["snap_everything"] > tot["lego"]
    assert tot["lego"] > tot["faceted"] == tot["dumb_solid"] == 0.0
    assert ok["good"] and not ok["snap_everything"] and not ok["lego"]
    assert not ok["faceted"] and not ok["dumb_solid"]
    # one key dim off of 7: 6/7 >= 80% => PASS by the 03_SCORING rule (S3 still drops)
    wd = scored[(pid, "wrong_dim")][0]
    assert wd["metrics"]["key_dims_ok"] == pytest.approx(6 / 7)
    assert ok["wrong_dim"] is True


@pytest.mark.parametrize("pid", list(VARIANTS))
def test_candidate_specifics(scored, pid):
    lego = scored[(pid, "lego")][0]
    assert lego["scores"]["S2"] == 0.0 and lego["scores"]["capped"] is True
    assert lego["scores"]["total"] == 40.0
    fac = scored[(pid, "faceted")][0]
    assert not fac["gate"]["ok"] and "faceted" in fac["gate"]["reason"]
    dumb = scored[(pid, "dumb_solid")][0]
    assert not dumb["gate"]["ok"] and "unmapped" in dumb["gate"]["reason"]
    wd, info, _ = scored[(pid, "wrong_dim")]
    changed = info["changed"][0]
    bad = [d for d in wd["details"]["S3"]["dims"] if not d["ok"]]
    assert [d["name"] for d in bad] == [changed["dim"]]
    assert abs(bad[0]["error"]) == pytest.approx(0.03, abs=1e-6)
    snap = scored[(pid, "snap_everything")][0]
    assert snap["metrics"]["key_dims_ok"] < 0.8
    # standard dims (plate_w, hole_pitch, boss_h) keep their credit: snapping does not move them
    assert snap["details"]["S3"]["standard_ok"] == 1.0


def test_scan_rules(scored):
    clean = scored[("syn_clean", "good")][0]["metrics"]
    scan = scored[("syn_scan", "good")][0]["metrics"]
    assert clean["band_tight_eff_mm"] == 0.1 and clean["band_wide_eff_mm"] == 0.3
    assert scan["band_tight_eff_mm"] == pytest.approx(0.15)  # 1.5 x floor p95 0.1
    assert scan["band_wide_eff_mm"] == 0.3
    assert "p_tight_inferred" not in clean  # synthetic: everything observable
    assert scan["p_tight_inferred"] == 1.0  # bottom face unobserved, reported separately
    det = scored[("syn_scan", "good")][0]["details"]["S1"]
    assert 0 < det["n_inferred"] < det["n_observable"]


def test_results_validate_and_are_ascii_lf(scored):
    for res, _, d in scored.values():
        validate_result(res)
        raw = (d / "result.json").read_bytes()
        assert b"\r\n" not in raw
        raw.decode("ascii")


def test_deterministic_bytes(scored, synth_gt_root, monkeypatch):
    """Same inputs => byte-identical result.json, also with a cold GT cache."""
    from recadbench_eval import score as score_mod

    monkeypatch.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    res, _, d = scored[("syn_scan", "snap_everything")]
    first = (d / "result.json").read_bytes()
    score_mod._GT_CACHE.clear()
    again = dumps(score_run(d, "syn_scan", CFG)).encode("ascii")
    assert again == first


def test_misaligned_prediction_recovers_via_pca(scored, synth_gt_root, monkeypatch, tmp_path):
    """A candidate exported in its own design frame (not the scanner frame) still aligns."""
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    _, _, d = scored[("syn_scan", "good")]
    shape = brep.load_step(d / "out" / "output.step")
    T = np.eye(4)
    T[:3, :3] = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
    T[:3, 3] = [30, 5, -2]
    (tmp_path / "out").mkdir()
    brep.write_step(brep.transform(shape, T), tmp_path / "out" / "output.step")
    (tmp_path / "out" / "features.json").write_bytes((d / "out" / "features.json").read_bytes())
    res = score_run(tmp_path, "syn_scan", CFG)
    assert res["details"]["alignment"]["init"].startswith("pca:")
    assert res["metrics"]["p_tight"] > 0.99
    assert res["alignment"]["datum_offset_deg"] > 45


def test_run_manifest_identity(scored):
    res, _, d = scored[("syn_clean", "good")]
    man = json.loads((d / "run_manifest.json").read_text(encoding="utf-8"))
    assert res["run_id"] == man["run_id"] and res["track"] == "E"
    assert res["skillset"]["id"] == "build123d@v1"
