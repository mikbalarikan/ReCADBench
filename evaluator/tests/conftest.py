"""Synthetic GT parts generated in-test (no files from ReCADBench-gt).

``synthetic_gt(kind)`` writes a GT folder (gt.step, gt_plan.json, key_dims.yaml, meta.yaml and,
for the scan variant, observability_mask.npz) into a temp ``RECADBENCH_GT_DIR``:

- ``clean``: ``source_kind: synthetic`` (identity frame, no floor, everything observable);
- ``scan``: ``source_kind: scan_rebuild`` with a known ``T_scan_to_gt``, a scan floor that widens
  the tight band (1.5 x 0.1 = 0.15 mm) and an observability mask (bottom face unobserved).

Part (GT frame, mm): plate 40 x 30 x 6.3 (z 0..6.3), boss D12.4 x 8 at (10, 0) with a D6.2 bore
through boss and plate, two D4.2 through holes at (-12, +-8). n_ref = 4, b1 = 3.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

PLATE_T, BOSS_D, BORE_D, HOLE_D, BOSS_H = 6.3, 12.4, 6.2, 4.2, 8.0
BOSS_C = (10.0, 0.0)
HOLES = [(-12.0, 8.0), (-12.0, -8.0)]


def build_shape():
    from build123d import Box, Cylinder, Pos

    top = PLATE_T + BOSS_H
    part = Pos(0, 0, PLATE_T / 2) * Box(40, 30, PLATE_T)
    part = part + Pos(BOSS_C[0], BOSS_C[1], PLATE_T + BOSS_H / 2) * Cylinder(BOSS_D / 2, BOSS_H)
    part = part - Pos(BOSS_C[0], BOSS_C[1], top / 2) * Cylinder(BORE_D / 2, top)
    for x, y in HOLES:
        part = part - Pos(x, y, PLATE_T / 2) * Cylinder(HOLE_D / 2, PLATE_T)
    return part.solids()[0]


def gt_plan(part_id: str) -> dict:
    top = PLATE_T + BOSS_H
    return {
        "spec_version": "0.3.0", "part_id": part_id, "revision": 1, "units": "mm",
        "part_family": "synthetic plate with boss and holes",
        "datums": [
            {"id": "D1", "role": "primary", "kind": "plane", "origin": [0, 0, 0],
             "direction": [0, 0, -1], "x_dir": [1, 0, 0], "basis": "bottom face"},
            {"id": "D2", "role": "secondary", "kind": "axis", "origin": [10, 0, 0],
             "direction": [0, 0, 1], "basis": "boss axis"},
        ],
        "reference_planes": [
            {"id": "P_BOT", "origin": [0, 0, 0], "normal": [0, 0, 1], "x_dir": [1, 0, 0],
             "basis": "bottom, normal up"},
            {"id": "P_TOP", "origin": [0, 0, PLATE_T], "normal": [0, 0, 1], "x_dir": [1, 0, 0],
             "basis": "plate top"},
        ],
        "parameters": [
            {"name": "plate_t", "value": PLATE_T}, {"name": "plate_w", "value": 40.0},
            {"name": "plate_h", "value": 30.0}, {"name": "boss_d", "value": BOSS_D},
            {"name": "boss_h", "value": BOSS_H}, {"name": "bore_d", "value": BORE_D},
            {"name": "hole_d", "value": HOLE_D}, {"name": "hole_pitch", "value": 16.0},
        ],
        "features": [
            {"id": "PLATE", "type": "extrude", "operation": "new",
             "sketch": {"plane": "P_BOT", "entities": [
                 {"kind": "rect", "center": [0, 0], "width": {"ref": "plate_w"},
                  "height": {"ref": "plate_h"}}]},
             "params": {"distance": {"ref": "plate_t"}, "direction": [0, 0, 1]}},
            {"id": "BOSS", "type": "extrude", "operation": "add",
             "sketch": {"plane": "P_TOP", "entities": [
                 {"kind": "circle", "center": [10, 0], "radius": BOSS_D / 2}]},
             "params": {"distance": {"ref": "boss_h"}, "direction": [0, 0, 1]}},
            {"id": "BORE", "type": "hole", "operation": "cut",
             "params": {"diameter": {"ref": "bore_d"}, "through": True, "kind": "simple",
                        "positions": [[10, 0, top]]}},
            {"id": "HOLES", "type": "hole", "operation": "cut",
             "params": {"diameter": {"ref": "hole_d"}, "through": True, "kind": "simple",
                        "positions": [[-12, 8, PLATE_T], [-12, -8, PLATE_T]]}},
        ],
    }


def key_dims(part_id: str) -> dict:
    top = PLATE_T + BOSS_H
    r_b = BOSS_D / 2
    return {"part_id": part_id, "dims": [
        {"name": "plate_t", "kind": "face_distance", "nominal": PLATE_T,
         "measure": {"face_type": "plane", "near_point": [-15, -10, 0],
                     "second": {"near_point": [-15, -10, PLATE_T]}}},
        {"name": "plate_w", "kind": "face_distance", "nominal": 40.0, "standard": True,
         "measure": {"face_type": "plane", "near_point": [-20, 0, 3],
                     "second": {"near_point": [20, 0, 3]}}},
        {"name": "boss_d", "kind": "diameter", "nominal": BOSS_D,
         "measure": {"face_type": "cylinder", "near_point": [10 + r_b, 0, PLATE_T + 4],
                     "axis_dir": [0, 0, 1]}},
        {"name": "bore_d", "kind": "diameter", "nominal": BORE_D, "editable": True,
         "measure": {"face_type": "cylinder", "near_point": [10 + BORE_D / 2, 0, PLATE_T + 4],
                     "axis_dir": [0, 0, 1]}},
        {"name": "hole_d", "kind": "diameter", "nominal": HOLE_D,
         "measure": {"face_type": "cylinder", "near_point": [-12 + HOLE_D / 2, 8, 3],
                     "axis_dir": [0, 0, 1]}},
        {"name": "hole_pitch", "kind": "axis_distance", "nominal": 16.0, "standard": True,
         "measure": {"face_type": "cylinder", "near_point": [-12 + HOLE_D / 2, 8, 3],
                     "axis_dir": [0, 0, 1],
                     "second": {"near_point": [-12 + HOLE_D / 2, -8, 3]}}},
        {"name": "boss_h", "kind": "face_distance", "nominal": BOSS_H, "standard": True,
         "measure": {"face_type": "plane", "near_point": [-15, -10, PLATE_T],
                     "second": {"near_point": [10, r_b - 1.0, top]}}},
    ]}


def rot(axis, deg) -> np.ndarray:
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    t = math.radians(deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K


T_SCAN = np.eye(4)
T_SCAN[:3, :3] = rot([0.3, -0.5, 0.8], 37.0)
T_SCAN[:3, 3] = [12.5, -40.0, 7.25]


def write_gt(root: Path, part_id: str, variant: str) -> Path:
    from recadbench_eval import brep

    folder = root / part_id
    folder.mkdir(parents=True, exist_ok=True)
    shape = build_shape()
    brep.write_step(shape, folder / "gt.step")
    recs = brep.face_records(shape)
    hist = {k: round(v, 4) for k, v in brep.face_type_areas(recs).items() if v > 0}
    meta = {
        "part_id": part_id, "tolerance_class": "B", "tier": 1, "split": "dev", "n_ref": 4,
        "faces_gt": len(recs), "face_types": hist, "b1": brep.genus(shape),
        "family": "synthetic plate", "gt_author": "human",
        "contamination": {"public_scan": False, "public_cad": False, "commercial_replica": False},
    }
    if variant == "clean":
        meta.update({"source_kind": "synthetic", "tessellation_chord_mm": 0.03})
    else:
        tess = brep.tessellate(shape, 0.01)
        from recadbench_eval.geom import sample

        smp = sample(tess, 20000, 5)
        obs = smp.points[:, 2] > 0.05  # the bottom face was never scanned
        np.savez_compressed(folder / "observability_mask.npz",
                            gt_points=smp.points.astype(np.float32), observable=obs)
        meta.update({
            "source_kind": "scan_rebuild",
            "scan": {"file": "scan_raw.stl", "sha256": hashlib.sha256(b"synthetic").hexdigest()},
            "registration": {"T_scan_to_gt": T_SCAN.tolist(), "method": "synthetic",
                             "rmse_mm": 0.0, "converged": True, "seed": 0},
            "scan_floor": {"scan_to_gt_p95_mm": 0.1, "gt_to_scan_observable_p95_mm": 0.1},
            "observability": {"observable_fraction": float(obs.mean()), "threshold_mm": 0.5,
                              "mask_file": "observability_mask.npz"},
        })
    (folder / "meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    (folder / "key_dims.yaml").write_text(yaml.safe_dump(key_dims(part_id), sort_keys=False),
                                          encoding="utf-8")
    (folder / "gt_plan.json").write_text(json.dumps(gt_plan(part_id), indent=2),
                                         encoding="utf-8")
    return folder


@pytest.fixture(scope="session")
def synth_gt_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("synthetic_gt")
    write_gt(root, "syn_clean", "clean")
    write_gt(root, "syn_scan", "scan")
    return root


@pytest.fixture()
def gt_env(synth_gt_root, monkeypatch) -> Path:
    monkeypatch.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    return synth_gt_root
