"""Public report of a scored submission (ADR 0006 section 6, leak rules).

The public row is built from an ALLOWLIST of fields, never by copying ``result.json``: no
key-dim nominal or measured value, no ``n_ref`` / ``faces_gt`` / ``b1_gt``, no GT volume or
extents, no ``gt_plan`` content, no registration, no GT-derived band, no file path. Gate
reasons are mapped from the failing check NAMES to fixed public sentences (the private reasons
carry GT numbers such as "x 38 GT faces" or "vs GT 123 mm3").

    task = public_task(result, status="ok")          # one per task id
    row = public_row(meta=..., sha256=..., ..., per_task={...})
    write_public(public_dir, row)                     # report.json + report.html

``render_heatmap`` (best effort, needs matplotlib) draws the PREDICTION tessellation coloured by
its distance to the GT surface; no GT geometry is drawn and the colour scale is fixed (not
derived from the GT band).
"""

from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path

ROUND = 4
STATUSES = ("ok", "gate_fail", "missing", "error")
META_PUBLIC = ("submitter_name", "submission_name", "agent_url", "model", "cad_backend",
               "harness", "human_in_loop")
ROW_KEYS = ("submission_id", "sha256", "eval_version", "layer", *META_PUBLIC, "open_total",
            "n_solved", "n_gate_ok", "per_task", "declared")
TASK_KEYS = ("status", "S1", "S2", "S3", "S4", "total", "gate_reason", "p_tight", "p_wide",
             "p95_mm", "key_dims", "face_ratio_bucket")
SCORE_KEYS = ("S1", "S2", "S3", "S4", "total")
MAX_TOTAL = 95.0
HEATMAP_VMAX_MM = 1.0  # fixed colour scale (never the GT-derived effective band)

# gate check name -> fixed public sentence (order = report order)
GATE_SENTENCES: dict[str, str] = {
    "loads": "output.step could not be loaded as a STEP solid",
    "one_solid": "output.step must contain exactly one solid",
    "valid": "the solid is not a valid B-rep (BRepCheck)",
    "closed": "the solid has an open shell",
    "b2": "the solid has internal voids (more than one shell)",
    "tessellation": "the solid could not be tessellated",
    "faceted": "faceted / mesh-like body (too many faces or triangle faces)",
    "volume": "volume differs from the reference by more than the 20 % scale limit (units?)",
    "features": "features.json missing or invalid (official layer)",
    "unmapped": "non-parametric body: features.json lists unmapped geometry (official layer)",
    "bbox": "aligned extents differ from the reference by more than 20 % (units / alignment?)",
    "freeform": "freeform cover-up: prismatic regions modelled as B-spline surfaces",
    "gt_copy": "suspected copy of the reference model",
}
STATUS_SENTENCES = {
    "missing": "no output.step submitted for this task",
    "timeout": "scoring timed out",
    "crash": "scoring failed (the STEP could not be processed)",
    "extract": "output.step could not be extracted from the zip",
}
_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


# ------------------------------------------------------------------------------------- helpers
def _num(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    if not math.isfinite(v):
        return None
    v = round(v, ROUND)
    return 0.0 if v == 0 else v


def face_ratio_bucket(q) -> str | None:
    q = _num(q)
    if q is None:
        return None
    # the same steps as parsimony.s2_face, so the public S2 carries no more than the bucket
    if q < 0.25:
        return "<0.25"
    if q < 0.35:
        return "0.25-0.35"
    if q <= 1.5:
        return "0.35-1.5"
    if q <= 2.0:
        return "1.5-2"
    if q <= 3.0:
        return "2-3"
    return ">3"


def _safe_name(s) -> str:
    return _NAME_RE.sub("_", str(s))[:80]


def gate_reason(result: dict) -> str:
    """Fixed public sentence(s) for the failing gate checks of a result (never its reason)."""
    checks = ((result.get("details") or {}).get("gate") or {}).get("checks") or {}
    failed = [k for k, v in checks.items() if v is False]
    known = [GATE_SENTENCES[k] for k in GATE_SENTENCES if k in failed]
    unknown = sorted(f"gate check '{_safe_name(k)}' failed" for k in failed
                     if k not in GATE_SENTENCES)
    out = known + unknown
    return "; ".join(out) if out else "gate failed"


def _key_dims(result: dict) -> list[dict]:
    dims = ((result.get("details") or {}).get("S3") or {}).get("dims") or []
    return [{"name": _safe_name(d.get("name")), "ok": bool(d.get("ok"))}
            for d in dims if isinstance(d, dict)]


def empty_task(status: str, reason: str | None) -> dict:
    assert status in STATUSES
    return {"status": status, "S1": 0.0, "S2": 0.0, "S3": 0.0, "S4": 0.0, "total": 0.0,
            "gate_reason": reason, "p_tight": None, "p_wide": None, "p95_mm": None,
            "key_dims": [], "face_ratio_bucket": None}


def public_task(result: dict) -> dict:
    """The public per-task entry of one ``result.json`` (allowlist)."""
    ok = bool((result.get("gate") or {}).get("ok"))
    task = empty_task("ok" if ok else "gate_fail", None if ok else gate_reason(result))
    scores = result.get("scores") or {}
    for k in SCORE_KEYS:
        task[k] = _num(scores.get(k)) or 0.0
    metrics = result.get("metrics") or {}
    if ok:
        task["p_tight"] = _num(metrics.get("p_tight"))
        task["p_wide"] = _num(metrics.get("p_wide"))
        task["p95_mm"] = _num(metrics.get("p95"))
        task["key_dims"] = _key_dims(result)
    q = metrics.get("face_ratio")
    if q is None:
        g = result.get("gate") or {}
        fp, fg = g.get("faces_pred"), g.get("faces_gt")
        if isinstance(fp, int) and isinstance(fg, int) and fg > 0:
            q = fp / fg
    task["face_ratio_bucket"] = face_ratio_bucket(q)
    return task


def declared_entry(result: dict) -> dict | None:
    d = (result.get("details") or {}).get("declared")
    if not isinstance(d, dict):
        return None
    out = {}
    for k in ("n_counted", "unmapped"):
        v = d.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out or None


def public_row(*, submission_id: str, sha256: str, eval_version: str, layer: str, meta: dict,
               task_ids: list[str], per_task: dict[str, dict], declared: dict[str, dict]
               ) -> dict:
    """The public row (= ``summary.json`` = ``report.json``); only ``ROW_KEYS``."""
    tasks = {tid: {k: per_task[tid].get(k) for k in TASK_KEYS} for tid in task_ids}
    totals = [tasks[t]["total"] or 0.0 for t in task_ids]
    row = {
        "submission_id": str(submission_id),
        "sha256": str(sha256),
        "eval_version": str(eval_version),
        "layer": str(layer),
        **{k: (bool(meta.get(k)) if k == "human_in_loop" else str(meta.get(k) or ""))
           for k in META_PUBLIC},
        "open_total": _num(sum(totals) / len(totals)) if totals else 0.0,
        "n_solved": sum(1 for t in tasks.values() if t["status"] != "missing"),
        "n_gate_ok": sum(1 for t in tasks.values() if t["status"] == "ok"),
        "per_task": tasks,
        "declared": {t: declared[t] for t in task_ids if declared.get(t)},
    }
    assert tuple(row) == ROW_KEYS
    return row


# ---------------------------------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--ok:#1f7a4d;--bad:#b3261e;
--warn:#8a6100;--card:#fff}
@media (prefers-color-scheme: dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#a3a29c;
--line:#33322f;--ok:#5cc28f;--bad:#f2867e;--warn:#e0b45a;--card:#1f1f1d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:32px 0 8px}
.mut{color:var(--mut)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.big{font-size:2.2rem;font-weight:600}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 16px;margin:16px 0}
dt{color:var(--mut)}dd{margin:0;overflow-wrap:anywhere}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
td.num{text-align:right}
.task{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;
margin:12px 0}
.task img{max-width:100%;height:auto;border-radius:4px}
code{font-size:.9em}
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def fmt(v) -> str:
    """The one number format of the HTML report (the leak test relies on it)."""
    if v is None:
        return "-"
    return f"{float(v):.2f}"


def _num_cell(v) -> str:
    return f'<td class="num">{fmt(v)}</td>'


def _status(s: str) -> str:
    cls = {"ok": "ok", "gate_fail": "bad", "error": "bad", "missing": "mut"}.get(s, "")
    return f'<span class="{cls}">{_e(s)}</span>'


def render_html(row: dict, renders: set[str] | None = None) -> str:
    """Self-contained ASCII HTML of the public row (``renders``: task ids with a PNG)."""
    renders = renders or set()
    url = row.get("agent_url") or ""
    link = (f'<a href="{_e(url)}" rel="nofollow noopener">{_e(url)}</a>'
            if url.startswith(("http://", "https://")) else "-")
    hil = ('<span class="warn">yes (human in the loop)</span>' if row["human_in_loop"]
           else "no")
    meta_rows = [("Submitter", _e(row["submitter_name"])), ("Model", _e(row["model"])),
                 ("CAD backend", _e(row["cad_backend"])), ("Harness", _e(row["harness"])),
                 ("Agent", link), ("Human in loop", hil), ("Layer", _e(row["layer"])),
                 ("Evaluator", _e(row["eval_version"])), ("Submission id",
                                                          _e(row["submission_id"])),
                 ("SHA-256", f"<code>{_e(row['sha256'])}</code>")]
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f"<title>{_e(row['submission_name'] or 'Submission')} - ReCADBench Open</title>",
           f"<style>{CSS}</style></head><body><main>",
           f"<h1>{_e(row['submission_name'] or 'Submission')}</h1>",
           '<p class="mut">ReCADBench Open report. Open layer: geometry-only scoring; S2 is the'
           " provisional face-count proxy (S2-face). PASS needs the Verified layer.</p>",
           f'<p><span class="big">{fmt(row["open_total"])}</span> <span class="mut">/ '
           f"{MAX_TOTAL:.0f} open total (mean over {len(row['per_task'])} tasks, missing = 0)"
           f"</span></p>",
           f'<p>{row["n_gate_ok"]} of {len(row["per_task"])} tasks passed the gate; '
           f'{row["n_solved"]} submitted.</p>',
           "<dl>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in meta_rows) + "</dl>",
           "<h2>Per task</h2>", '<div class="scroll"><table><thead><tr><th>Task</th>'
           "<th>Status</th><th>S1 /40</th><th>S2 /25</th><th>S3 /20</th><th>S4 /10</th>"
           "<th>Total /95</th><th>Face-count ratio</th></tr></thead><tbody>"]
    for tid, t in row["per_task"].items():
        out.append(f"<tr><td><code>{_e(tid)}</code></td><td>{_status(t['status'])}</td>"
                   + "".join(_num_cell(t[k]) for k in SCORE_KEYS)
                   + f"<td>{_e(t['face_ratio_bucket'] or '-')}</td></tr>")
    out.append("</tbody></table></div>")
    out.append("<h2>Details</h2>")
    for tid, t in row["per_task"].items():
        out.append(f'<section class="task"><h3><code>{_e(tid)}</code> '
                   f"{_status(t['status'])}</h3>")
        if t["gate_reason"]:
            out.append(f'<p class="bad">{_e(t["gate_reason"])}</p>')
        if t["status"] == "ok":
            out.append('<div class="scroll"><table><tbody>'
                       f"<tr><th>Points within tight band</th>{_num_cell(t['p_tight'])}</tr>"
                       f"<tr><th>Points within wide band</th>{_num_cell(t['p_wide'])}</tr>"
                       f"<tr><th>Deviation P95 (mm)</th>{_num_cell(t['p95_mm'])}</tr>"
                       "</tbody></table></div>")
            if t["key_dims"]:
                out.append('<p class="mut">Key dimensions (within tolerance or not; nominal '
                           "values are not published)</p><ul>")
                out.extend(f"<li><code>{_e(d['name'])}</code>: "
                           + ('<span class="ok">within</span>' if d["ok"]
                              else '<span class="bad">not within</span>') + "</li>"
                           for d in t["key_dims"])
                out.append("</ul>")
        if tid in renders:
            out.append(f'<img src="renders/{_e(tid)}.png" alt="deviation map of the submitted '
                       f'model for {_e(tid)}" loading="lazy">')
        d = row["declared"].get(tid)
        if d:
            out.append('<p class="mut">Declared features.json (not scored): '
                       + ", ".join(f"{_e(k)} {_e(v)}" for k, v in d.items()) + "</p>")
        out.append("</section>")
    out.append("</main></body></html>\n")
    return "\n".join(out).encode("ascii", "xmlcharrefreplace").decode("ascii")


def write_public(public_dir: str | Path, row: dict) -> tuple[Path, Path]:
    d = Path(public_dir)
    d.mkdir(parents=True, exist_ok=True)
    renders = {p.stem for p in (d / "renders").glob("*.png")} if (d / "renders").is_dir() \
        else set()
    renders &= set(row["per_task"])
    pj = d / "report.json"
    pj.write_bytes(dumps(row).encode("ascii"))
    ph = d / "report.html"
    ph.write_bytes(render_html(row, renders).encode("ascii"))
    return pj, ph


def dumps(row: dict) -> str:
    return json.dumps(row, indent=2, sort_keys=False, ensure_ascii=True) + "\n"


# ------------------------------------------------------------------------------------- heatmap
def _result_T_total(result_path, T_scan_to_gt):
    """``T_total`` of the score child (``T_fine @ T_scan_to_gt``, see ``align.align``) from a
    private ``result.json``; None when it carries no alignment."""
    import numpy as np

    if not result_path or not Path(result_path).is_file():
        return None
    res = json.loads(Path(result_path).read_text(encoding="utf-8"))
    T_fine = ((res.get("details") or {}).get("alignment") or {}).get("T_fine")
    if T_fine is None:
        return None
    T_fine = np.asarray(T_fine, dtype=float)
    if T_fine.shape != (4, 4) or not np.isfinite(T_fine).all():
        return None
    return T_fine @ np.asarray(T_scan_to_gt, dtype=float)


def render_heatmap(run_dir: str | Path, part_id: str, cfg, out_png: str | Path,
                   vmax_mm: float = HEATMAP_VMAX_MM, result_path: str | Path | None = None
                   ) -> Path:
    """Prediction tessellation (in the submitter's own scanner frame) coloured by the distance
    of each triangle centroid to the GT surface after the evaluator's alignment. Only the
    prediction is drawn; the colour scale is fixed at ``0..vmax_mm``. With ``result_path`` (the
    score child's private ``result.json``) its alignment is reused instead of a second ICP."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import colors
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    from . import brep
    from .align import align
    from .score import gt_context

    ctx = gt_context(part_id, cfg)
    pred = brep.load_step(Path(run_dir) / "out" / "output.step")
    sols = brep.solids(pred)
    if len(sols) != 1:
        raise ValueError("heatmap needs exactly one solid")
    T_total = _result_T_total(result_path, ctx.part.T_scan_to_gt)
    if T_total is None:
        tess_scan = brep.tessellate(sols[0], ctx.chord, cfg.angular_rad)
        T_total = align(tess_scan, ctx.part.T_scan_to_gt, ctx.surface, ctx.samples.points,
                        ctx.bands.tight, n=cfg.icp_samples, seed=cfg.seed + 11).T_total
    view = brep.tessellate(sols[0], max(ctx.chord, 0.05), cfg.angular_rad)
    tri = view.tri_xyz
    dist, *_ = ctx.surface.query(brep.apply(T_total, tri.mean(axis=1)),
                                 beyond_mm=2.0 * vmax_mm)
    norm = colors.Normalize(vmin=0.0, vmax=vmax_mm, clip=True)
    cmap = plt.get_cmap("viridis")
    fig = plt.figure(figsize=(6, 5), dpi=120)
    ax = fig.add_subplot(projection="3d")
    ax.add_collection3d(Poly3DCollection(tri, facecolors=cmap(norm(dist)), linewidths=0))
    lo, hi = view.vertices.min(axis=0), view.vertices.max(axis=0)
    c, r = (lo + hi) / 2, float((hi - lo).max()) / 2 or 1.0
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.set_axis_off()
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    fig.colorbar(sm, ax=ax, shrink=0.7, label=f"distance to reference (mm, clipped at "
                                                f"{vmax_mm:g})")
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return out
