"""Golden tests on the R385 pilots (evaluator host only; skip without RECADBENCH_GT_DIR).

Candidates are generated at test time into pytest temp dirs from each pilot's gt.step (they are
derived from GT: never committed) and exported in the scanner frame with inv(T_scan_to_gt), like
a real prediction. Asserts the ordering and PASS flags of 03_SCORING:

    good > wrong_dim, snap_everything > lego (capped 40) > faceted = dumb_solid = 0 (gate)

``RECADBENCH_PILOT_TABLE=<path>`` also writes the markdown result table (for STATUS.md / PRs).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from recadbench_eval import brep, golden, gtstore
from recadbench_eval.config import EvalConfig
from recadbench_eval.report import markdown
from recadbench_eval.score import score_run, validate_result

PILOTS = ("r385_hs01_motor_mount", "r385_dr02_wobble_plate", "r385_hs04_pump_head")
# the candidates are gt.step by construction: the GT-copy backstop is off for the scores and
# checked on its own in test_pilot_gt_copy_flags_golden
GOLDEN_CFG = EvalConfig(gt_copy_check=False)

pytestmark = pytest.mark.skipif(
    not gtstore.available() or not all(
        (Path(os.environ.get("RECADBENCH_GT_DIR", "")) / p / "gt.step").is_file() for p in PILOTS
    ),
    reason="evaluator host only: RECADBENCH_GT_DIR with the R385 pilots",
)


@pytest.fixture(scope="module")
def pilot_results(tmp_path_factory):
    out = {}
    for pid in PILOTS:
        part = gtstore.load_part(pid)
        shape = brep.load_step(part.step_path)
        for kind in golden.KINDS:
            d = tmp_path_factory.mktemp(f"{pid}_{kind}")
            info = golden.build(kind, d, part_id=pid, gt_shape=shape, gt_plan=part.plan,
                                key_dims=part.key_dims, meta=part.meta,
                                T_scan_to_gt=part.T_scan_to_gt)
            out[(pid, kind)] = (score_run(d, pid, GOLDEN_CFG), info, d)
    table = os.environ.get("RECADBENCH_PILOT_TABLE")
    if table:
        Path(table).write_text(markdown([r for r, _, _ in out.values()], "R385 pilot goldens"),
                               encoding="utf-8", newline="\n")
    return out


@pytest.mark.parametrize("pid", PILOTS)
def test_pilot_good_passes(pilot_results, pid):
    res, _, _ = pilot_results[(pid, "good")]
    validate_result(res)
    assert res["gate"]["ok"] and res["pass"]
    assert res["metrics"]["p_tight"] >= 0.95 and res["metrics"]["p_wide"] >= 0.99
    assert res["metrics"]["key_dims_ok"] == 1.0
    assert res["scores"]["total"] > 94.9


@pytest.mark.parametrize("pid", PILOTS)
def test_pilot_ordering_and_pass(pilot_results, pid):
    r = {k: pilot_results[(pid, k)][0] for k in golden.KINDS}
    tot = {k: v["scores"]["total"] for k, v in r.items()}
    for v in r.values():
        validate_result(v)
    assert tot["good"] > tot["wrong_dim"] > tot["lego"]
    assert tot["good"] > tot["snap_everything"] > tot["lego"]
    assert tot["lego"] == 40.0 and r["lego"]["scores"]["capped"]
    assert tot["faceted"] == tot["dumb_solid"] == 0.0
    assert "faceted" in r["faceted"]["gate"]["reason"]
    assert "unmapped" in r["dumb_solid"]["gate"]["reason"]
    assert r["good"]["pass"]
    for k in ("snap_everything", "lego", "faceted", "dumb_solid"):
        assert not r[k]["pass"], k
    # one wrong key dim of 8-10 stays >= 80%: PASS under the 03_SCORING rule, S3 drops
    wd = r["wrong_dim"]
    assert wd["metrics"]["key_dims_ok"] < 1.0
    assert wd["pass"] == (wd["metrics"]["key_dims_ok"] >= 0.8)
    assert r["snap_everything"]["metrics"]["key_dims_ok"] < 0.8


@pytest.mark.parametrize("pid", PILOTS)
def test_pilot_band_eff_and_inferred(pilot_results, pid):
    res, _, _ = pilot_results[(pid, "good")]
    meta = gtstore.load_part(pid).meta
    floor = meta["scan_floor"]["scan_to_gt_p95_mm"]
    assert res["metrics"]["band_tight_eff_mm"] == pytest.approx(max(0.1, 1.5 * floor), abs=1e-6)
    assert res["metrics"]["band_wide_eff_mm"] == pytest.approx(max(0.3, 1.5 * floor), abs=1e-6)
    assert "p_tight_inferred" in res["metrics"]
    share = res["details"]["S1"]["observable_share_gt_samples"]
    assert share == pytest.approx(meta["observability"]["observable_fraction"], abs=0.02)


@pytest.mark.parametrize("pid", PILOTS)
def test_pilot_gt_copy_flags_golden(pilot_results, pid):
    """The golden candidates are gt.step: with the GT-copy backstop on (the default) the
    GT-geometry ones fail the gate as a suspected copy; the measurements are in details."""
    for kind in ("good", "lego"):
        _, _, d = pilot_results[(pid, kind)]
        res = score_run(d, pid)
        assert not res["gate"]["ok"] and "suspected GT copy" in res["gate"]["reason"], kind
        gc = res["details"]["gt_copy"]
        assert gc["suspected"] and gc["enforced"]
        assert gc["p95_all_mm"] < gc["p95_limit_mm"]
