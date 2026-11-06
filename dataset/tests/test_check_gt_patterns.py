"""check_gt_patterns.py on a synthetic part with known ground truth (CI)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("build123d")

from check_gt_patterns import check_part, main  # noqa: E402

PLAN = {
    "spec_version": "0.3.0",
    "part_id": "synthetic_disk",
    "revision": 1,
    "units": "mm",
    "datums": [
        {
            "id": "D1",
            "role": "primary",
            "kind": "plane",
            "origin": [0, 0, 0],
            "direction": [0, 0, 1],
            "x_dir": [1, 0, 0],
        },
        {
            "id": "D2",
            "role": "secondary",
            "kind": "axis",
            "origin": [0, 0, 0],
            "direction": [0, 0, 1],
        },
    ],
    "parameters": [{"name": "n", "value": 3}],
    "features": [
        {
            "id": "F1",
            "type": "extrude",
            "operation": "new",
            "sketch": {
                "plane": "D1",
                "entities": [{"kind": "circle", "center": [0, 0], "radius": 30}],
            },
            "params": {"distance": 10, "direction": [0, 0, 1]},
        },
        {
            "id": "F2",
            "type": "hole",
            "operation": "cut",
            "params": {
                "diameter": 6,
                "through": True,
                "kind": "simple",
                "positions": [[0, 20, 10]],
            },
            "refs": ["F1:plane:+Z:max"],
        },
        {
            "id": "F3",
            "type": "pattern_circular",
            "params": {"features": ["F2"], "axis": "D2", "count": {"ref": "n"}, "pitch_deg": 60},
        },
    ],
}


@pytest.fixture(scope="module")
def step(tmp_path_factory) -> Path:
    """Disk R30 x 10 with three Ø6 holes on r = 20 at 90, 150, 210 deg (seed at +Y)."""
    from build123d import BuildPart, Cylinder, Hole, Locations, PolarLocations, export_step

    with BuildPart() as bp:
        Cylinder(30, 10)
        with Locations((0, 0, 5)):
            with PolarLocations(20, 3, start_angle=90, angular_range=120, endpoint=True):
                Hole(3)
    out = tmp_path_factory.mktemp("gt") / "gt.step"
    export_step(bp.part, str(out))
    return out


def _part(tmp_path: Path, step: Path, plan: dict) -> Path:
    (tmp_path / "gt.step").write_bytes(step.read_bytes())
    (tmp_path / "gt_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return tmp_path


def test_pitch_plan_matches(tmp_path, step) -> None:
    (chk,) = check_part(_part(tmp_path, step, PLAN))
    assert chk.ok and chk.count == 3 and chk.step_deg == 60
    (c,) = chk.candidates
    assert c.status == "match" and sorted(c.found) == [-150.0, 90.0, 150.0]
    assert main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "params",
    [
        {"count": {"ref": "n"}, "angle_deg": 360},  # 0.3.0 full circle: 90 / 210 / 330
        {"count": {"ref": "n"}, "angle_deg": 120},  # total sweep 120: step 40
        {"count": 2, "pitch_deg": 60},  # one instance too few
        {"count": {"ref": "n"}, "pitch_deg": -60},  # wrong sense: 90 / 30 / -30
    ],
)
def test_wrong_pattern_is_reported(tmp_path, step, params) -> None:
    plan = copy.deepcopy(PLAN)
    plan["features"][2]["params"] = {"features": ["F2"], "axis": "D2", **params}
    (chk,) = check_part(_part(tmp_path, step, plan))
    assert not chk.ok and chk.candidates[0].status == "mismatch"
    assert main([str(tmp_path)]) == 1


def test_unobserved_seed_is_not_a_match(tmp_path, step) -> None:
    plan = copy.deepcopy(PLAN)
    plan["features"][1]["params"]["diameter"] = 9  # no Ø9 cylinder in the STEP
    (chk,) = check_part(_part(tmp_path, step, plan))
    assert chk.candidates[0].status == "unobserved" and not chk.ok
