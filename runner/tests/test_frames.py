"""Track T frame mapping: known answers (hand-computed) + plan_score round trip."""

from __future__ import annotations

import copy
import math

import pytest
from recadbench_run.frames import (
    compose_rigid,
    invert_rigid,
    map_plan,
    map_selector,
    rotation_about,
    seed_from_gt,
)

# T_scan_to_gt: rotate +90 deg about Z, then translate by (10, 20, 30):
#   p_gt = R p_scan + t,  R = [[0,-1,0],[1,0,0],[0,0,1]]
# so inv(T): p_scan = R^T (p_gt - t),  R^T = [[0,1,0],[-1,0,0],[0,0,1]]
T90 = [[0.0, -1.0, 0.0, 10.0], [1.0, 0.0, 0.0, 20.0], [0.0, 0.0, 1.0, 30.0],
       [0.0, 0.0, 0.0, 1.0]]


def gt_plan() -> dict:
    return {
        "spec_version": "0.3.0", "part_id": "syn", "revision": 1, "units": "mm",
        "datums": [
            {"id": "D1", "role": "primary", "kind": "plane", "origin": [1, 2, 3],
             "direction": [0, 0, -1], "x_dir": [1, 0, 0]},
            {"id": "D2", "role": "secondary", "kind": "axis", "origin": [5, 0, 0],
             "direction": [0, 0, 1]},
        ],
        "reference_planes": [{"id": "P1", "origin": [0, 0, 6], "normal": [0, 1, 0],
                              "x_dir": [1, 0, 0]}],
        "parameters": [{"name": "w", "value": 40.0}, {"name": "n", "value": 4}],
        "features": [
            {"id": "F1", "type": "extrude", "operation": "new",
             "sketch": {"plane": "P1", "entities": [{"kind": "rect", "center": [3, 4],
                                                     "width": {"ref": "w"}, "height": 30}]},
             "params": {"distance": 6, "direction": [0, 1, 0]}},
            {"id": "F2", "type": "hole", "operation": "cut",
             "params": {"diameter": 4.2, "through": True, "kind": "simple",
                        "positions": [[5, 0, 0], [-5, 0, 0]]},
             "refs": ["F1:plane:+X:max"]},
            {"id": "F3", "type": "pattern_circular",
             "params": {"features": ["F2"], "axis": [1, 0, 0], "count": {"ref": "n"},
                        "angle_deg": 360}},
            {"id": "F4", "type": "primitive", "operation": "add",
             "params": {"shape": "cylinder", "size": [6, 8], "center": [0, 0, 0],
                        "axis": "D2"}},
            {"id": "F5", "type": "fillet", "params": {"radius": 1,
                                                      "edges": "F1:edges:concave"}},
            {"id": "F6", "type": "pattern_linear",
             "params": {"features": ["F4"], "direction": [0, 0, 1], "count": 2,
                        "spacing": 10, "direction2": "F1:plane:+Z:min"}},
        ],
    }


def test_inverse_known_answer():
    inv = invert_rigid(T90)
    assert inv == [[0.0, 1.0, 0.0, -20.0], [-1.0, 0.0, 0.0, 10.0], [0.0, 0.0, 1.0, -30.0],
                   [0.0, 0.0, 0.0, 1.0]]
    eye = compose_rigid(T90, inv)
    assert all(abs(eye[i][j] - (i == j)) < 1e-12 for i in range(4) for j in range(4))


def test_map_plan_known_answers():
    plan = gt_plan()
    m, notes = map_plan(plan, invert_rigid(T90))
    d1, d2 = m["datums"]
    assert d1["origin"] == [-18.0, 9.0, -27.0]       # R^T((1,2,3) - t)
    assert d1["direction"] == [0.0, 0.0, -1.0]       # z unchanged
    assert d1["x_dir"] == [0.0, -1.0, 0.0]           # R^T e_x
    assert d2["origin"] == [-20.0, 5.0, -30.0]
    p1 = m["reference_planes"][0]
    assert p1["origin"] == [-20.0, 10.0, -24.0]
    assert p1["normal"] == [1.0, 0.0, 0.0]           # R^T e_y
    assert p1["x_dir"] == [0.0, -1.0, 0.0]
    f = {x["id"]: x for x in m["features"]}
    assert f["F1"]["params"]["direction"] == [1.0, 0.0, 0.0]
    assert f["F1"]["sketch"] == plan["features"][0]["sketch"]   # sketch coords are local
    assert f["F2"]["params"]["positions"] == [[-20.0, 5.0, -30.0], [-20.0, 15.0, -30.0]]
    assert f["F2"]["refs"] == ["F1:plane:-Y:max"]                # +X -> R^T e_x = -Y
    assert f["F3"]["params"]["axis"] == [0.0, -1.0, 0.0]
    assert f["F3"]["params"]["count"] == {"ref": "n"}
    assert f["F4"]["params"]["center"] == [-20.0, 10.0, -30.0]
    assert f["F4"]["params"]["axis"] == "D2"                      # a datum id stays
    assert f["F4"]["params"]["size"] == [6, 8]
    assert f["F5"]["params"]["edges"] == "F1:edges:concave"       # edge sets are invariant
    assert f["F6"]["params"]["direction"] == [0.0, 0.0, 1.0]
    assert f["F6"]["params"]["direction2"] == "F1:plane:+Z:min"
    assert notes == []
    assert m["parameters"] == plan["parameters"]
    assert plan == gt_plan()  # input untouched


def test_selector_off_axis_keeps_meaning_in_note():
    T = rotation_about([0, 0, 1], 30)
    sel, vec = map_selector(T, "BODY:plane:+X:max")
    assert sel == "BODY:plane:any:any" and vec is not None
    assert vec[0] == pytest.approx(math.cos(math.radians(30)))
    assert vec[1] == pytest.approx(math.sin(math.radians(30)))
    m, notes = map_plan(gt_plan(), T)
    f2 = next(x for x in m["features"] if x["id"] == "F2")
    assert f2["refs"] == ["F1:plane:any:any"]
    assert "direction +X of the reference frame" in f2["evidence"]["note"]
    assert len(notes) == 1 and notes[0].startswith("F2:")
    assert map_selector(T, "F1:plane:+Z:max:2") == ("F1:plane:+Z:max:2", None)
    assert map_selector(T, "F1:cylinder:axis:any") == ("F1:cylinder:axis:any", None)


def test_rejects_non_rigid():
    bad = copy.deepcopy(T90)
    bad[0][0] = 2.0
    with pytest.raises(ValueError):
        map_plan(gt_plan(), bad)
    mirror = [[-1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]
    with pytest.raises(ValueError, match="reflection"):
        invert_rigid(mirror)


def test_round_trip_general_rotation():
    T = compose_rigid([[1, 0, 0, 12.5], [0, 1, 0, -40], [0, 0, 1, 7.25], [0, 0, 0, 1]],
                      rotation_about([0.3, -0.5, 0.8], 37.0))
    m, _ = map_plan(gt_plan(), invert_rigid(T))
    back, _ = map_plan(m, T)
    for a, b in zip(back["datums"], gt_plan()["datums"], strict=True):
        assert a["origin"] == pytest.approx(b["origin"], abs=1e-8)
        assert a["direction"] == pytest.approx(b["direction"], abs=1e-8)


def test_seed_is_a_valid_revision_1():
    from recadbench_spec import validate_document

    plan = gt_plan()
    plan["revision"] = 3
    plan["change_log"] = ["x"]
    seed, _ = seed_from_gt(plan, T90)
    assert seed["revision"] == 1 and "change_log" not in seed
    rep = validate_document(seed, "re_plan_ir")
    assert rep.ok, [str(e) for e in rep.errors]
    ident, _ = seed_from_gt(gt_plan(), None)
    assert ident == gt_plan()


def test_plan_score_round_trip():
    """The mapped seed plan scored with T_scan_to_gt == the GT plan scored in the GT frame."""
    pytest.importorskip("recadbench_eval")
    from recadbench_eval.plan_score import plan_score
    from runner_helpers import evaluator_conftest

    ec = evaluator_conftest()
    gt = ec.gt_plan("syn_scan")
    kd = ec.key_dims("syn_scan")
    T = ec.T_SCAN.tolist()
    seed, _ = seed_from_gt(gt, T)
    assert seed["datums"][0]["direction"] != gt["datums"][0]["direction"]
    ref = plan_score(gt, gt, kd, 0.02, None)
    got = plan_score(seed, gt, kd, 0.02, ec.T_SCAN)
    assert got["plan_score"] == pytest.approx(ref["plan_score"])
    assert got["components"] == pytest.approx(ref["components"])
    assert got["datum"]["angle_deg"] == pytest.approx(0.0, abs=1e-5)
    # and without the registration the datum points are lost (the mapping is not a no-op)
    wrong = plan_score(seed, gt, kd, 0.02, None)
    assert wrong["components"]["datums"] < ref["components"]["datums"]
