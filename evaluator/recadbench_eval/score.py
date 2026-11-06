"""Score one run dir -> ``result.json`` (validated against the spec ``result`` schema).

Run dir contract (what the worker's ``recadbench finish`` leaves; P10 writes it)::

    run_manifest.json            # optional here (ids, track); CLI flags fill the gaps
    out/output.step              # prediction, input (= scanner) frame, mm
    out/features.json            # adapter extraction on the worker (never agent self-report)
    plan/re_plan.v<N>.json       # IR revisions (plan score uses the highest N)
    out/s5/edits.json            # S5 set-param job output (only read with --s5)

Pipeline: gate (static) -> align (T_scan_to_gt + fine ICP) -> gate (freeform) -> S1 -> S2 ->
S3 -> S4 -> [S5] -> plan score -> total (capped at 40 when r > 3) -> PASS.
Ground truth comes only from ``RECADBENCH_GT_DIR`` (``gtstore``). Deterministic: fixed seeds,
floats rounded to 1e-6 and sorted keys, so the same inputs give a byte-identical result.json.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import __version__, brep, gtstore
from .align import align
from .config import CLASS_KEYDIM_TOL, EvalConfig, bands
from .deviation import Bands, effective_bands, observable_labels, s1
from .gate import GateResult, bbox_gate, freeform_gate, static_gate
from .geom import Samples, Surface, sample
from .intent import measure_key_dims, relations, score_s3
from .parsimony import s2
from .plan_score import ir_adherence, plan_drift, plan_score
from .topology import s4

PLAN_RE = re.compile(r"^re_plan\.v([0-9]+)\.json$")


# --------------------------------------------------------------------------------- GT context
@dataclass
class GtContext:
    part: gtstore.GtPart
    shape: object
    recs: list
    tess: brep.Tess
    surface: Surface
    samples: Samples
    kinds: np.ndarray
    observable: np.ndarray
    hist: dict
    b1: int
    chord: float
    bands: Bands
    tol: float
    volume: float


_GT_CACHE: dict = {}


def gt_context(part_id: str, cfg: EvalConfig) -> GtContext:
    part = gtstore.load_part(part_id)
    key = (str(part.folder.resolve()), part.step_path.stat().st_mtime_ns, cfg)
    if key in _GT_CACHE:
        return _GT_CACHE[key]
    tcls = part.meta["tolerance_class"]
    cb = bands(tcls)
    chord = cfg.chord_frac * cb[0]
    shape = brep.load_step(part.step_path)
    recs = brep.face_records(shape)
    tess = brep.tessellate(shape, chord, cfg.angular_rad)
    smp = sample(tess, cfg.n_samples, cfg.seed)
    ctx = GtContext(
        part=part, shape=shape, recs=recs, tess=tess, surface=Surface(tess), samples=smp,
        kinds=np.array([recs[i].kind for i in smp.face_id]),
        observable=observable_labels(smp.points, part.mask_points, part.mask_observable),
        hist=brep.face_type_areas(recs), b1=int(part.meta.get("b1", brep.genus(shape))),
        chord=chord, bands=effective_bands(cb, part.floor_p95, cfg.k_floor),
        tol=CLASS_KEYDIM_TOL[tcls], volume=abs(brep.volume(shape)),
    )
    _GT_CACHE[key] = ctx
    return ctx


# ------------------------------------------------------------------------------------ helpers
def _rnd(x):
    if isinstance(x, dict):
        return {str(k): _rnd(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_rnd(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = round(float(x), 6)
        return 0.0 if v == 0 else v
    if isinstance(x, np.ndarray):
        return _rnd(x.tolist())
    return x


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _json(p: Path) -> dict | None:
    """Run-dir JSON; ``utf-8-sig`` accepts the BOM Windows PowerShell 5.1 writes."""
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else None


def read_features(p: Path) -> tuple[dict | None, str | None]:
    """``(features, error)``: an unreadable or spec-invalid features.json is a gate failure
    (03_SCORING section 1: the counts come from the adapter; an inconsistent extraction, e.g. a
    ``count`` that disagrees with the ``counted`` rows, is not scoreable)."""
    from recadbench_spec import validate_document

    if not p.is_file():
        return None, None
    try:
        doc = _json(p)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"features.json unreadable ({type(exc).__name__}: {exc})"
    if not isinstance(doc, dict):
        return None, "features.json unreadable (not a JSON object)"
    rep = validate_document(doc, "features")
    if not rep.ok:
        errs = "; ".join(str(e) for e in rep.errors[:3])
        return None, f"features.json invalid against the spec: {errs}"
    return doc, None


def near_point_limit(cfg: EvalConfig, b: Bands) -> float:
    """How far a key-dim / relation face may lie from its GT probe point (mm)."""
    return max(cfg.near_point_max_mm, cfg.near_point_band_k * b.wide)


def plan_files(run_dir: Path) -> list[Path]:
    d = run_dir / "plan"
    if not d.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in d.iterdir() if (m := PLAN_RE.match(p.name))]
    return [p for _, p in sorted(found)]


def _features_path(run_dir: Path) -> Path:
    p = run_dir / "out" / "features.json"
    return p if p.is_file() else run_dir / "features.json"


def _identity(run_dir: Path, manifest: dict | None, part_id: str | None, track: str | None
              ) -> dict:
    m = manifest or {}
    pid = part_id or m.get("part_id")
    if not pid:
        raise ValueError("part id unknown: pass --part or provide run_manifest.json")
    out = {
        "run_id": m.get("run_id", run_dir.name),
        "track": track or m.get("track", "E"),
        "part_id": pid,
        "strategy": {k: v for k, v in (m.get("strategy") or {"id": "unknown"}).items()
                     if k in ("id", "git_sha")},
        "model": {k: v for k, v in (m.get("model") or {"id": "unknown"}).items()
                  if k in ("id", "provider")},
        "backend": {k: v for k, v in (m.get("backend") or {"id": "unknown"}).items()
                    if k in ("id", "software_version")},
        "skillset": {k: v for k, v in (m.get("skillset") or {"id": "unknown"}).items()
                     if k in ("id", "git_sha")},
    }
    if "replicate" in m:
        out["replicate"] = int(m["replicate"])
    return out


def _versions() -> dict:
    from recadbench_spec import SPEC_VERSION

    return {"spec": SPEC_VERSION, "evaluator": __version__}


def _zero(res: dict, g: GateResult, details: dict) -> dict:
    res["gate"] = {"ok": False, "reason": g.reason or "gate failed"}
    if g.faces_pred is not None:
        res["gate"]["faces_pred"] = g.faces_pred
    if g.faces_gt is not None:
        res["gate"]["faces_gt"] = g.faces_gt
    res["scores"].update({"S1": 0.0, "S2": 0.0, "S3": 0.0, "S4": 0.0, "total": 0.0,
                          "capped": False})
    res["pass"] = False
    details["gate"] = {"checks": g.checks, "reasons": g.reasons}
    return res


# -------------------------------------------------------------------------------------- score
def score_run(run_dir: str | Path, part_id: str | None = None, cfg: EvalConfig | None = None,
              *, track: str | None = None, plan_path: str | Path | None = None) -> dict:
    cfg = cfg or EvalConfig()
    run_dir = Path(run_dir)
    manifest = _json(run_dir / "run_manifest.json")
    res = _identity(run_dir, manifest, part_id, track)
    res["versions"] = _versions()
    res["scores"] = {"S5": None, "plan_score": None}
    eff = _json(run_dir / "efficiency.json")
    if eff:
        res["efficiency"] = eff
    details: dict = {"config": {"seed": cfg.seed, "n_samples": cfg.n_samples,
                                "k_floor": cfg.k_floor, "s5": cfg.s5}}
    res["details"] = details
    ctx = gt_context(res["part_id"], cfg)
    meta = ctx.part.meta
    metrics: dict = {"band_tight_eff_mm": ctx.bands.tight, "band_wide_eff_mm": ctx.bands.wide}
    res["metrics"] = metrics

    # ---- plan score (every track that has an IR)
    plans = [Path(plan_path)] if plan_path else plan_files(run_dir)
    if plans and ctx.part.plan is not None:
        final = _json(plans[-1])
        ps = plan_score(final, ctx.part.plan, ctx.part.key_dims, ctx.tol, ctx.part.T_scan_to_gt)
        res["scores"]["plan_score"] = ps["plan_score"]
        details["plan"] = {"file": plans[-1].name, **ps}
        if len(plans) > 1:
            metrics["plan_drift"] = plan_drift(_json(plans[0]), final)
    if res["track"] == "S":
        res["gate"] = {"ok": False, "reason": "plan-only run (Track S): no geometry scored"}
        res["pass"] = False
        return _rnd(res)

    g = GateResult()
    step = run_dir / "out" / "output.step"
    feats_p = _features_path(run_dir)
    features, feats_err = read_features(feats_p)
    details["inputs"] = {"output.step": _sha(step), "features.json": _sha(feats_p)}
    try:
        pred = brep.load_step(step)
    except ValueError as exc:
        g.fail("loads", str(exc))
        return _rnd(_zero(res, g, details))
    g.passed("loads")
    sols = brep.solids(pred)
    pred_one = sols[0] if len(sols) == 1 else pred
    recs_scan = brep.face_records(pred_one)
    try:
        tess_scan = brep.tessellate(pred_one, ctx.chord, cfg.angular_rad)
    except ValueError:
        tess_scan = None
    static_gate(
        g, n_solids=len(sols), valid=brep.is_valid(pred), closed=brep.shells_closed(pred),
        recs=recs_scan, n_triangles_planar=brep.triangle_face_count(recs_scan),
        faces_gt=int(meta["faces_gt"]), pred_volume=brep.volume(pred_one),
        gt_volume=ctx.volume, features=features, cfg=cfg,
        n_shells=len(sols[0].shells()) if len(sols) == 1 else 0, features_error=feats_err,
    )
    if features is not None and features.get("face_count") is not None:
        # report only: the adapter counts native faces, a STEP round trip may split periodic
        # faces, so a mismatch is not by itself proof of a foreign features.json
        details["inputs"]["face_count_features"] = int(features["face_count"])
        details["inputs"]["face_count_step"] = len(recs_scan)
    if features is not None:
        metrics["ir_adherence"] = ir_adherence(ctx.part.plan, features) if ctx.part.plan else None
        if metrics["ir_adherence"] is None:
            del metrics["ir_adherence"]
    if not g.ok or tess_scan is None:
        if tess_scan is None:
            g.fail("tessellation", "candidate has no triangulation")
        return _rnd(_zero(res, g, details))

    # ---- alignment
    al = align(tess_scan, ctx.part.T_scan_to_gt, ctx.surface, ctx.samples.points,
               ctx.bands.tight, n=cfg.icp_samples, seed=cfg.seed + 11)
    res["alignment"] = {"rmse": al.rmse, "datum_offset_mm": al.datum_offset_mm,
                        "datum_offset_deg": al.datum_offset_deg}
    details["alignment"] = {"init": al.init, "f1": al.f1, "converged": al.converged,
                            "T_fine": al.T_fine.tolist()}
    pred_gt = brep.transform(pred_one, al.T_total)
    recs = brep.face_records(pred_gt)
    tess = brep.tessellate(pred_gt, ctx.chord, cfg.angular_rad)
    psmp = sample(tess, cfg.n_samples, cfg.seed + 1)
    psurf = Surface(tess)
    pkinds = np.array([recs[i].kind for i in psmp.face_id])
    bbox_gate(g, tess.vertices, ctx.tess.vertices, cfg)
    freeform_gate(g, tier=int(meta["tier"]), gt_pts=ctx.samples.points, gt_kind=ctx.kinds,
                  pred_pts=psmp.points, pred_kind=pkinds, cfg=cfg)
    if not g.ok:
        return _rnd(_zero(res, g, details))
    res["gate"] = {"ok": True, "faces_pred": g.faces_pred, "faces_gt": g.faces_gt}
    details["gate"] = {"checks": g.checks, "reasons": []}

    # ---- S1
    r1 = s1(ctx.samples, ctx.observable, ctx.surface, psmp, psurf, ctx.bands, cfg)
    for k in ("p_tight", "p_wide", "mean", "rms", "p95", "max", "hausdorff",
              "p_tight_inferred", "p_wide_inferred"):
        if r1.get(k) is not None:
            metrics[k] = r1[k]
    details["S1"] = {"targets_met": r1["targets_met"], **r1["detail"],
                     "band_tight_class_mm": ctx.bands.tight_class,
                     "band_wide_class_mm": ctx.bands.wide_class}

    # ---- S3 key dims (needed by S2 parametric presence)
    lim = near_point_limit(cfg, ctx.bands)
    dims = measure_key_dims(ctx.part.key_dims, recs, ctx.tol, cfg, max_dist=lim)
    rels = relations(ctx.recs, recs, ctx.surface, ctx.samples.points, psurf, psmp.points,
                     ctx.bands.tight_class, cfg, max_dist=lim)
    s3v, s3d = score_s3(dims, rels)
    metrics["key_dims_ok"] = s3d["key_dims_ok"]
    details["S3"] = {**s3d, "dims": [d.as_dict() for d in dims], "relations_detail": rels}

    # ---- S2
    r2 = s2(features, int(meta["n_ref"]), len(recs), int(meta["faces_gt"]), dims, ctx.tol, cfg)
    metrics.update({"r_feat": r2["r_feat"], "face_ratio": r2["face_ratio"],
                    "param_coverage": r2["param_coverage"]})
    details["S2"] = {k: r2[k] for k in ("n_pred", "n_ref", "uncovered_key_dims", "halved_by")}

    # ---- S4
    r4 = s4(brep.genus(pred_gt), ctx.b1, brep.face_type_areas(recs), ctx.hist)
    metrics.update({"b1_match": r4["b1_match"], "face_type_tvd": r4["face_type_tvd"]})
    details["S4"] = {"b1_pred": r4["b1_pred"], "b1_gt": r4["b1_gt"],
                     "face_types_pred": r4["face_types_pred"]}

    # ---- S5 (flag)
    total = r1["S1"] + r2["S2"] + s3v + r4["S4"]
    if cfg.s5:
        from .editability import s5

        r5 = s5(run_dir, pred_one, al.T_total, ctx.part.key_dims, ctx.recs,
                ctx.bands.tight_class, ctx.chord, cfg)
        res["scores"]["S5"] = r5["S5"]
        details["S5"] = r5
        total += r5["S5"]
    capped = bool(r2["cap_total"] and total > cfg.total_cap_lego)
    if r2["cap_total"]:
        total = min(total, cfg.total_cap_lego)
    res["scores"].update({"S1": r1["S1"], "S2": r2["S2"], "S3": s3v, "S4": r4["S4"],
                          "total": total, "capped": capped})
    pass_checks = {
        "gate": True,
        "s1_targets": r1["targets_met"],
        "r_in_range": cfg.pass_r_min <= r2["r_feat"] <= cfg.pass_r_max,
        "key_dims": s3d["key_dims_ok"] >= cfg.pass_key_dims - 1e-12,
    }
    res["pass"] = all(pass_checks.values())
    details["pass_checks"] = pass_checks
    return _rnd(res)


def validate_result(res: dict) -> None:
    from recadbench_spec import validate_document

    rep = validate_document(res, "result")
    if not rep.ok:
        raise ValueError("result.json invalid: " + "; ".join(str(e) for e in rep.errors))


def dumps(res: dict) -> str:
    return json.dumps(res, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_result(res: dict, path: str | Path) -> Path:
    validate_result(res)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(dumps(res).encode("ascii"))
    return p
