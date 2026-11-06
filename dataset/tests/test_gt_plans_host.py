"""Host-only: every GT plan under RECADBENCH_GT_DIR validates against the current spec and its
circular patterns (count, step, seed angle) match the cylinders in its gt.step (ADR 0004).

Skips cleanly without RECADBENCH_GT_DIR (CI, workers) or without build123d. Reads GT in place;
nothing is copied or printed beyond pass/fail details.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

GT_DIR = os.environ.get("RECADBENCH_GT_DIR")
PARTS = (
    sorted(p for p in Path(GT_DIR).iterdir() if (p / "gt_plan.json").is_file())
    if GT_DIR and Path(GT_DIR).is_dir()
    else []
)

pytestmark = pytest.mark.skipif(not PARTS, reason="RECADBENCH_GT_DIR with GT parts not set")


@pytest.mark.parametrize("part", PARTS, ids=lambda p: p.name)
def test_gt_plan_valid_for_current_spec(part: Path) -> None:
    from recadbench_spec import validate_file

    report = validate_file(part / "gt_plan.json")
    assert report.ok, [str(i) for i in report.errors]


@pytest.mark.parametrize("part", PARTS, ids=lambda p: p.name)
def test_gt_circular_patterns_match_gt_step(part: Path) -> None:
    pytest.importorskip("build123d")
    if not (part / "gt.step").is_file():
        pytest.skip("no gt.step")
    from check_gt_patterns import check_part

    checks = check_part(part)
    bad = [
        (c.feature, [(x.seed, x.source, x.status, x.expected, x.found) for x in c.candidates])
        for c in checks
        if not c.ok
    ]
    assert not bad, bad
