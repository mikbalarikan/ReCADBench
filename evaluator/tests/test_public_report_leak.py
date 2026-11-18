"""Leak test for the public report (ADR 0006 section 6): on a synthetic GT part, build the
public report from open- and official-layer results (ok and gate-fail) and assert that no
key-dim nominal, no ``faces_gt`` / ``n_ref`` value next to its name, no ``gt_plan`` parameter
value, no GT volume and no host path appears in ``report.html`` / ``report.json``."""

from __future__ import annotations

import json
import re

import pytest
from recadbench_eval import brep, golden, gtstore
from recadbench_eval.config import EvalConfig
from recadbench_eval.public_report import fmt, public_row, public_task, write_public
from recadbench_eval.score import score_run

CFG = EvalConfig(n_samples=8000, icp_samples=4000, gt_copy_check=False)
PID = "syn_scan"
META = {"submitter_name": "Leak Lab", "submission_name": "leak test", "agent_url": "",
        "model": "m", "cad_backend": "b", "harness": "h", "human_in_loop": True,
        "notes": "n", "agree_to_publish": True}

# numeric leaves allowed in report.json (key path patterns)
ALLOWED_NUMERIC = [
    re.compile(r"^(open_total|n_solved|n_gate_ok)$"),
    re.compile(r"^per_task\.[^.]+\.(S1|S2|S3|S4|total|p_tight|p_wide|p95_mm)$"),
    re.compile(r"^declared\.[^.]+\.(n_counted|unmapped)$"),
]
FORBIDDEN_KEYS = {"nominal", "measured", "error", "faces_gt", "faces_pred", "n_ref", "b1_gt",
                  "b1_pred", "registration", "T_scan_to_gt", "band_tight_eff_mm",
                  "band_wide_eff_mm", "volume", "gt_plan", "details", "metrics", "alignment",
                  "reason", "dims", "parameters"}


def _build(kind, d, scale=None):
    part = gtstore.load_part(PID)
    shape = brep.load_step(part.step_path)
    if scale is not None:
        shape = shape.scale(scale)
    golden.build(kind, d, part_id=PID, gt_shape=shape, gt_plan=part.plan,
                 key_dims=part.key_dims, meta=part.meta, T_scan_to_gt=part.T_scan_to_gt,
                 cfg=CFG)
    return d


@pytest.fixture(scope="module")
def report(synth_gt_root, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    try:
        base = tmp_path_factory.mktemp("leak")
        results = {
            # the open layer enforces the GT-copy backstop: a near copy (0.2 % larger) scores,
            # the exact copy fails the gate
            "t_open_good": score_run(_build("good", base / "n", scale=1.002), PID, CFG,
                                     layer="open"),
            "t_open_copy": score_run(_build("good", base / "g"), PID, CFG, layer="open"),
            "t_open_wrong": score_run(_build("wrong_dim", base / "w", scale=1.002), PID, CFG,
                                      layer="open"),
            "t_open_faceted": score_run(_build("faceted", base / "f"), PID, CFG, layer="open"),
            "t_open_scaled": score_run(_build("good", base / "s", scale=1.6), PID, CFG,
                                       layer="open"),
            "t_official_good": score_run(base / "g", PID, CFG),
            "t_official_lego": score_run(_build("lego", base / "l"), PID, CFG),
        }
        assert results["t_open_good"]["gate"]["ok"]
        assert not results["t_open_faceted"]["gate"]["ok"]
        assert not results["t_open_copy"]["gate"]["ok"]
        assert not results["t_open_scaled"]["gate"]["ok"]
        assert "GT" in results["t_open_scaled"]["gate"]["reason"]  # the private reason leaks
        per_task = {k: public_task(r) for k, r in results.items()}
        declared = {"t_open_good": {"n_counted": 4, "unmapped": 0}}
        row = public_row(submission_id="sub-test", sha256="0" * 64, eval_version="0+open.x",
                         layer="open", meta=META, task_ids=list(results), per_task=per_task,
                         declared=declared)
        pub = base / "public"
        (pub / "renders").mkdir(parents=True)
        (pub / "renders" / "t_open_good.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        write_public(pub, row)
        part = gtstore.load_part(PID)
        yield {"row": row, "json": (pub / "report.json").read_text(encoding="ascii"),
               "html": (pub / "report.html").read_text(encoding="ascii"), "part": part,
               "paths": [str(base), str(synth_gt_root)], "results": results}
    finally:
        mp.undo()


def _forbidden_numbers(part) -> list[float]:
    vals = [float(d["nominal"]) for d in part.key_dims["dims"]]
    vals += [float(p["value"]) for p in part.plan["parameters"]
             if isinstance(p.get("value"), (int, float))]
    return sorted(set(vals))


def _tokens(v: float) -> list[str]:
    return [f"{v:.{n}f}" for n in range(1, 5)]


def _hits(text: str, values) -> list[str]:
    out = []
    for v in values:
        for tok in _tokens(v):
            if re.search(r"(?<![\d.])" + re.escape(tok) + r"(?!\d)", text):
                out.append(tok)
    return out


def _walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, x


def _keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from _keys(v)


def test_json_numeric_leaves_are_allowlisted(report):
    doc = json.loads(report["json"])
    assert doc == report["row"]
    for path, v in _walk(doc):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert any(p.match(path) for p in ALLOWED_NUMERIC), path


def test_json_has_no_private_keys(report):
    doc = json.loads(report["json"])
    bad = set(_keys(doc)) & FORBIDDEN_KEYS
    assert not bad, bad


def test_json_strings_have_no_gt_numbers(report):
    doc = json.loads(report["json"])
    strings = " | ".join(v for _, v in _walk(doc) if isinstance(v, str))
    assert not _hits(strings, _forbidden_numbers(report["part"]))
    reasons = " | ".join(t["gate_reason"] or "" for t in doc["per_task"].values())
    assert not re.search(r"\d", reasons.replace("20 %", "")), reasons


def test_html_has_no_gt_values(report):
    html = report["html"]
    # numeric cells hold only allowlisted public-row numbers in the one report format
    cells = re.findall(r'<td class="num">([^<]*)</td>', html)
    allowed = {fmt(v) for p, v in _walk(report["row"])
               if isinstance(v, (int, float)) and not isinstance(v, bool)} | {"-"}
    assert cells and set(cells) <= allowed
    big = re.findall(r'<span class="big">([^<]*)</span>', html)
    assert big == [fmt(report["row"]["open_total"])]
    stripped = re.sub(r'<td class="num">[^<]*</td>', "", html)
    stripped = re.sub(r'<span class="big">[^<]*</span>', "", stripped)
    assert not _hits(stripped, _forbidden_numbers(report["part"]))


def test_no_gt_counts_volume_or_paths(report):
    part = report["part"]
    meta = part.meta
    for text in (report["json"], report["html"]):
        for name in ("faces_gt", "n_ref", "GT faces", "b1"):
            assert not re.search(re.escape(name) + r"\W{0,12}\d", text), name
        assert not re.search(r"\b" + str(meta["faces_gt"]) + r"\s*(GT\s*)?faces", text)
        assert "mm3" not in text and "registration" not in text
        assert "nominal" not in text.replace("nominal values are not published", "")
        for p in report["paths"]:
            assert p not in text and p.replace("\\", "/") not in text
        assert not re.search(r"[A-Za-z]:\\\\|[A-Za-z]:\\", text)


def test_gt_volume_never_appears(report):
    from recadbench_eval.score import gt_context

    vol = gt_context(PID, CFG).volume
    toks = [f"{vol:.{n}f}" for n in range(0, 5)] + [f"{vol:.6g}"]
    for text in (report["json"], report["html"]):
        assert not any(t in text for t in toks)


def test_gate_reasons_are_fixed_sentences(report):
    from recadbench_eval.public_report import GATE_SENTENCES

    pt = report["row"]["per_task"]
    assert pt["t_open_faceted"]["gate_reason"] == GATE_SENTENCES["faceted"]
    assert GATE_SENTENCES["volume"] in pt["t_open_scaled"]["gate_reason"]
    assert pt["t_open_copy"]["gate_reason"] == GATE_SENTENCES["gt_copy"]
    for t in pt.values():
        if t["gate_reason"]:
            parts = t["gate_reason"].split("; ")
            assert all(p in GATE_SENTENCES.values() for p in parts), t["gate_reason"]


def test_key_dims_only_name_and_ok(report):
    names = {d["name"] for d in report["part"].key_dims["dims"]}
    pt = report["row"]["per_task"]
    for tid in ("t_open_good", "t_open_wrong", "t_official_good"):
        kd = pt[tid]["key_dims"]
        assert kd and {d["name"] for d in kd} <= names
        assert all(set(d) == {"name", "ok"} for d in kd)
    assert any(not d["ok"] for d in pt["t_open_wrong"]["key_dims"])


def test_report_html_is_ascii_and_links_render(report):
    report["html"].encode("ascii")
    assert 'src="renders/t_open_good.png"' in report["html"]


@pytest.mark.parametrize(("faces_pred", "faces_gt"),
                         [(15, 11), (7, 11), (40, 23), (41, 23), (5, 11), (6, 11)])
def test_faces_gt_not_derivable_from_public_s2(faces_pred, faces_gt):
    """S2-face is a step function of the public face-ratio bucket: the public S2 (and the
    total minus S1/S3/S4) carry no information beyond the bucket, so faces_gt cannot be
    inverted from them."""
    from recadbench_eval.parsimony import s2_face

    r = s2_face(faces_pred, faces_gt)
    res = {"gate": {"ok": True, "faces_pred": faces_pred, "faces_gt": faces_gt},
           "scores": {"S1": 30.0, "S2": r["S2"], "S3": 10.0, "S4": 5.0, "total": 45.0 + r["S2"]},
           "metrics": {"face_ratio": r["face_ratio"]}, "details": {}}
    t = public_task(res)
    step = {"<0.25": 0.0, "0.25-0.35": 10.0, "0.35-1.5": 25.0, "1.5-2": 15.0, "2-3": 5.0,
            ">3": 0.0}
    assert t["S2"] == step[t["face_ratio_bucket"]]
    assert t["total"] - t["S1"] - t["S3"] - t["S4"] == pytest.approx(step[t["face_ratio_bucket"]])
    assert "faces_gt" not in json.dumps(t)
