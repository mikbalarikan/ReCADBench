"""S5 editability (5, behind ``--s5`` / ``EvalConfig.s5``; enabled from M5). 03_SCORING §3 S5.

The edit itself runs on a backend host (queue job ``recad <b> set-param <native> <name> <value>``,
00_DECISIONS D7). Its output lands in the run dir as::

    out/s5/edits.json  {"edits": [{"key_dim": <name>, "param": <native name>,
                                   "old": <value>, "new": <value>,
                                   "step": "s5/<file>.step", "ok": <rebuild succeeded>}]}

For each edit of a key dim (``key_dims.editable: true`` dims are the designated ones) the
evaluator checks, in the prediction frame:

1. the rebuild succeeded and the edited STEP is one valid closed solid;
2. the volume changes in the expected direction (from the GT: does growing that dim add or
   remove material — ``intent.dim_handle``);
3. outside the edited region (candidate samples farther than ``region_mm`` from the face that
   carries the dim) the edited model stays within the class tight band at p95.

``S5 = 5 x passed / edits``; no edits file => 0 (with a note) when S5 is enabled.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import brep
from .config import EvalConfig
from .geom import Surface, sample
from .intent import dim_handle, find_face

REGION_MIN_MM = 1.0


def s5(run_dir: Path, pred_scan, T_total: np.ndarray, key_dims: dict, gt_recs, tight_band: float,
       chord: float, cfg: EvalConfig) -> dict:
    path = run_dir / "out" / "s5" / "edits.json"
    if not path.is_file():
        return {"S5": 0.0, "note": "no out/s5/edits.json (S5 job output missing)", "edits": []}
    edits = (json.loads(path.read_text(encoding="utf-8")) or {}).get("edits") or []
    dims = {d["name"]: d for d in key_dims.get("dims") or []}
    T_inv = np.linalg.inv(T_total)
    pred_tess = brep.tessellate(pred_scan, chord, cfg.angular_rad)
    pts = sample(pred_tess, 20000, cfg.seed + 7)
    pred_recs = brep.face_records(pred_scan)
    v_pred = brep.volume(pred_scan)
    rows = []
    for e in edits:
        row = {"key_dim": e.get("key_dim"), "param": e.get("param"), "ok": False}
        rows.append(row)
        d = dims.get(e.get("key_dim"))
        if d is None:
            row["note"] = "not a key dim"
            continue
        if not e.get("ok"):
            row["note"] = "rebuild failed"
            continue
        step = run_dir / "out" / str(e.get("step", ""))
        try:
            ed = brep.load_step(step)
        except ValueError as exc:
            row["note"] = str(exc)
            continue
        if len(brep.solids(ed)) != 1 or not brep.is_valid(ed) or not brep.shells_closed(ed):
            row["note"] = "edited STEP is not one valid closed solid"
            continue
        h = dim_handle(d, gt_recs, cfg)
        if h is None:
            row["note"] = "dim has no single-face handle; direction unknown"
            continue
        _, foot_gt, k = h
        dx = float(e["new"]) - float(e["old"])
        dv = brep.volume(ed) - v_pred
        row["dV"] = dv
        row["direction_ok"] = bool(np.sign(dv) == np.sign(k * dx)) and abs(dv) > 1e-9
        # edited region: the candidate face nearest the GT handle point (prediction frame)
        foot = brep.apply(T_inv, foot_gt[None])[0]
        rec, _, _ = find_face(pred_recs, foot, None, None)
        if rec is None:
            row["note"] = "no candidate face for the dim"
            continue
        region = max(REGION_MIN_MM, 3.0 * abs(dx))
        dface = Surface(brep.tessellate(rec.face, chord, cfg.angular_rad)).query(
            pts.points, beyond_mm=region * 2)[0]
        far = dface > region
        dev = Surface(brep.tessellate(ed, chord, cfg.angular_rad)).query(pts.points[far])[0]
        p95 = float(np.quantile(dev, 0.95)) if len(dev) else 0.0
        row["outside_p95_mm"] = p95
        row["outside_ok"] = p95 <= tight_band
        row["ok"] = bool(row["direction_ok"] and row["outside_ok"])
    n = len(rows)
    return {"S5": 5.0 * sum(r["ok"] for r in rows) / n if n else 0.0, "edits": rows}
