"""``recadbench-eval score-submission``: zip -> per-task child scoring -> public report
(ADR 0006, contract section 3). Synthetic GT parts only (conftest)."""

from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import sys
import zipfile
from pathlib import Path

import pytest
from recadbench_eval import brep, golden, gtstore, submit_score
from recadbench_eval.cli import main
from recadbench_eval.config import EvalConfig
from recadbench_eval.public_report import (
    GATE_SENTENCES,
    ROW_KEYS,
    STATUS_SENTENCES,
    TASK_KEYS,
    face_ratio_bucket,
    gate_reason,
    render_heatmap,
)
from recadbench_eval.submit_score import (
    check_task_ids,
    config_hash,
    eval_version,
    read_tasks_file,
    score_submission,
)

CFG_DICT = {"n_samples": 8000, "icp_samples": 4000, "gt_copy_check": False}
TASKS = ["syn_clean", "syn_scan", "syn_missing", "syn_nogt"]
META = {"submitter_name": "Test Lab", "submission_name": "unit <b>run</b>",
        "agent_url": "https://example.org/agent", "model": "test-model",
        "cad_backend": "build123d", "harness": "pytest", "human_in_loop": False,
        "notes": "synthetic", "agree_to_publish": True}


def _candidate(kind: str, pid: str, d: Path, scale: float | None = None) -> Path:
    part = gtstore.load_part(pid)
    shape = brep.load_step(part.step_path)
    if scale is not None:
        shape = shape.scale(scale)
    golden.build(kind, d, part_id=pid, gt_shape=shape, gt_plan=part.plan, key_dims=part.key_dims,
                 meta=part.meta, T_scan_to_gt=part.T_scan_to_gt,
                 cfg=EvalConfig(**CFG_DICT))
    return d


def _zip(path: Path, entries: dict[str, bytes], meta: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta or META))
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


@pytest.fixture(scope="module")
def scored(synth_gt_root, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    try:
        base = tmp_path_factory.mktemp("subscore")
        # 0.2 % larger than GT: the open layer enforces the GT-copy backstop
        good = _candidate("good", "syn_clean", base / "good", scale=1.002)
        fac = _candidate("faceted", "syn_scan", base / "faceted")
        entries = {
            "syn_clean/output.step": (good / "out" / "output.step").read_bytes(),
            "syn_clean/features.json": (good / "out" / "features.json").read_bytes(),
            "syn_scan/output.step": (fac / "out" / "output.step").read_bytes(),
            "syn_nogt/output.step": (good / "out" / "output.step").read_bytes(),
        }
        z = _zip(base / "submission.zip", entries)
        cfg = base / "eval.json"
        cfg.write_text(json.dumps(CFG_DICT), encoding="utf-8")
        tasks = base / "tasks.txt"
        tasks.write_text("# synthetic\n" + "\n".join(TASKS) + "\n", encoding="utf-8")
        out = base / "D"
        rc = main(["score-submission", "--zip", str(z), "--out-dir", str(out), "--tasks-file",
                   str(tasks), "--config", str(cfg), "--timeout", "600"])
        yield {"rc": rc, "out": out, "zip": z, "cfg": cfg, "base": base}
    finally:
        mp.undo()


def test_cli_ok_and_files(scored):
    assert scored["rc"] == 0
    out = scored["out"]
    for rel in ("public/report.json", "public/report.html", "summary.json",
                "private/syn_clean/result.json", "private/syn_scan/result.json",
                "runs/syn_clean/out/output.step", "runs/syn_clean/run_manifest.json"):
        assert (out / rel).is_file(), rel
    assert (out / "summary.json").read_bytes() == (out / "public/report.json").read_bytes()
    assert not (out / "runs" / "syn_missing").exists()
    assert not (out / "error.json").exists()


def test_run_manifest_and_private_result(scored):
    out = scored["out"]
    row = json.loads((out / "summary.json").read_text(encoding="ascii"))
    sha8 = row["sha256"][:8]
    man = json.loads((out / "runs/syn_clean/run_manifest.json").read_text(encoding="ascii"))
    assert man == {"part_id": "syn_clean", "run_id": f"sub-{sha8}-syn_clean", "track": "E"}
    res = json.loads((out / "private/syn_clean/result.json").read_text(encoding="ascii"))
    assert res["run_id"] == f"sub-{sha8}-syn_clean" and res["track"] == "E"
    assert res["details"]["layer"] == "open"
    assert res["pass"] is False


def test_public_row_shape_and_values(scored):
    row = json.loads((scored["out"] / "summary.json").read_text(encoding="ascii"))
    assert tuple(row) == ROW_KEYS
    assert list(row["per_task"]) == TASKS
    for t in row["per_task"].values():
        assert tuple(t) == TASK_KEYS
    pt = row["per_task"]
    assert pt["syn_clean"]["status"] == "ok", pt["syn_clean"]
    assert pt["syn_clean"]["total"] > 50
    assert pt["syn_clean"]["face_ratio_bucket"] == "0.35-1.5"
    assert pt["syn_clean"]["key_dims"] and all(set(d) == {"name", "ok"}
                                               for d in pt["syn_clean"]["key_dims"])
    assert pt["syn_scan"]["status"] == "gate_fail"
    assert pt["syn_scan"]["gate_reason"] == GATE_SENTENCES["faceted"]
    assert pt["syn_scan"]["face_ratio_bucket"] == ">3"
    assert pt["syn_missing"]["status"] == "missing" and pt["syn_missing"]["total"] == 0.0
    assert pt["syn_nogt"]["status"] == "error"
    assert "failed" in pt["syn_nogt"]["gate_reason"]
    assert row["n_solved"] == 3 and row["n_gate_ok"] == 1
    assert row["open_total"] == round(pt["syn_clean"]["total"] / len(TASKS), 4)
    assert row["submission_id"] == f"sub-{row['sha256'][:8]}"
    assert row["layer"] == "open"
    cfg = EvalConfig(**CFG_DICT)
    assert row["eval_version"] == eval_version(cfg, "open")
    assert re.fullmatch(r"\d+\.\d+\.\d+\+open\.[0-9a-f]{8}", row["eval_version"])
    assert row["submitter_name"] == "Test Lab" and row["human_in_loop"] is False
    assert set(row["declared"]) == {"syn_clean"}
    assert set(row["declared"]["syn_clean"]) <= {"n_counted", "unmapped"}
    assert "notes" not in row  # meta allowlist


def test_private_error_and_public_reason_have_no_internals(scored):
    out = scored["out"]
    assert (out / "private/syn_nogt/error.txt").is_file()
    raw = (out / "public/report.json").read_text(encoding="ascii")
    assert str(scored["base"]) not in raw and "Traceback" not in raw


def test_html_escapes_user_text_and_links_renders(scored):
    html = (scored["out"] / "public/report.html").read_text(encoding="ascii")
    assert "<b>run</b>" not in html and "&lt;b&gt;run&lt;/b&gt;" in html
    assert 'href="https://example.org/agent"' in html
    png = scored["out"] / "public/renders/syn_clean.png"
    if importlib.util.find_spec("matplotlib") is not None:
        assert png.is_file() and png.read_bytes()[:4] == b"\x89PNG"
        assert 'src="renders/syn_clean.png"' in html
    assert not (scored["out"] / "public/renders/syn_scan.png").exists()


def test_rejected_zip_writes_error_json(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("meta.json", json.dumps(META))
        zf.writestr("stray.txt", "x")
    out = tmp_path / "D"
    rc = main(["score-submission", "--zip", str(z), "--out-dir", str(out), "--no-heatmap"])
    assert rc == 2
    err = json.loads((out / "error.json").read_text(encoding="ascii"))
    assert "stray.txt" in err["error"]
    assert not (out / "summary.json").exists()


def test_timeout_is_an_error_row(gt_env, tmp_path):
    z = _zip(tmp_path / "s.zip", {"syn_clean/output.step": b"ISO-10303-21;\n"})
    row = score_submission(z, tmp_path / "D", task_ids=["syn_clean"], timeout=0.01,
                           heatmap=False, log=lambda *_: None)
    t = row["per_task"]["syn_clean"]
    assert t["status"] == "error" and t["gate_reason"] == "scoring timed out"
    assert row["open_total"] == 0.0 and row["n_solved"] == 1 and row["n_gate_ok"] == 0


def test_default_task_ids_all_missing(gt_env, tmp_path):
    z = _zip(tmp_path / "s.zip", {})
    row = score_submission(z, tmp_path / "D", heatmap=False, log=lambda *_: None)
    assert len(row["per_task"]) == 10
    assert all(t["status"] == "missing" for t in row["per_task"].values())
    assert row["open_total"] == 0.0 and row["n_solved"] == 0


def test_helpers(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("﻿a # first\n\nb\na\n", encoding="utf-8")
    assert read_tasks_file(f) == ["a", "b"]
    assert face_ratio_bucket(0.24) == "<0.25" and face_ratio_bucket(0.3) == "0.25-0.35"
    assert face_ratio_bucket(0.35) == "0.35-1.5" and face_ratio_bucket(1.5) == "0.35-1.5"
    assert face_ratio_bucket(2.0) == "1.5-2" and face_ratio_bucket(3.0) == "2-3"
    assert face_ratio_bucket(3.01) == ">3" and face_ratio_bucket(None) is None
    assert config_hash(EvalConfig()) != config_hash(EvalConfig(n_samples=1))
    res = {"details": {"gate": {"checks": {"volume": False, "loads": True, "bbox": False,
                                           "weird check!": False}}},
           "gate": {"ok": False, "reason": "volume 1 vs GT 123 mm3"}}
    r = gate_reason(res)
    assert r.startswith(GATE_SENTENCES["volume"]) and GATE_SENTENCES["bbox"] in r
    assert "123" not in r and "gate check 'weird_check_' failed" in r
    assert gate_reason({"gate": {"ok": False}}) == "gate failed"


# ------------------------------------------------------------------ review follow-ups (REP-FIX)
def _fake_result(sha8: str, tid: str) -> dict:
    return {"part_id": tid, "run_id": f"sub-{sha8}-{tid}", "track": "E", "pass": False,
            "gate": {"ok": True}, "scores": {"S1": 30.0, "S2": 25.0, "S3": 10.0, "S4": 5.0,
                                              "total": 70.0},
            "metrics": {"p_tight": 0.9, "p_wide": 0.99, "p95": 0.1, "face_ratio": 1.0},
            "details": {"layer": "open", "S3": {"dims": []}}}


def _fake_children(monkeypatch, heatmap_outcome: str = "ok") -> list[list[str]]:
    """Replace the child processes: ``score`` writes a fake result.json, ``render-heatmap``
    writes a PNG and returns ``heatmap_outcome``. Returns the recorded argument lists."""
    calls: list[list[str]] = []

    def fake(args, timeout, python=None):
        calls.append([*args, f"timeout={timeout:g}"])
        out = Path(args[args.index("--out") + 1])
        if args[0] == "score":
            tid = args[args.index("--part") + 1]
            out.write_text(json.dumps(_fake_result("x", tid)), encoding="ascii")
            return "ok", ""
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x89PNG\r\n\x1a\npartial")
        return heatmap_outcome, "boom"

    monkeypatch.setattr(submit_score, "run_child", fake)
    return calls


def test_corrupt_deflate_entry_is_an_extract_error(gt_env, tmp_path):
    payload = "".join(random.Random(1).choice("ABCDEF0123456789;\n") for _ in range(20000))
    z = _zip(tmp_path / "s.zip", {"syn_clean/output.step": payload.encode("ascii")})
    with zipfile.ZipFile(z) as zf:
        info = zf.getinfo("syn_clean/output.step")
    raw = bytearray(z.read_bytes())
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for i in range(start + 16, start + info.compress_size - 16):
        raw[i] ^= 0x5A
    z.write_bytes(bytes(raw))
    row = score_submission(z, tmp_path / "D", task_ids=["syn_clean"], heatmap=False,
                           log=lambda *_: None)
    t = row["per_task"]["syn_clean"]
    assert t["status"] == "error" and t["gate_reason"] == STATUS_SENTENCES["extract"]
    assert (tmp_path / "D/private/syn_clean/error.txt").is_file()
    assert not (tmp_path / "D/private/syn_clean/result.json").exists()


@pytest.mark.skipif(importlib.util.find_spec("matplotlib") is None, reason="no matplotlib")
def test_failed_heatmap_child_drops_png(gt_env, tmp_path, monkeypatch):
    calls = _fake_children(monkeypatch, heatmap_outcome="crash")
    z = _zip(tmp_path / "s.zip", {"syn_clean/output.step": b"ISO-10303-21;\n"})
    row = score_submission(z, tmp_path / "D", task_ids=["syn_clean"], timeout=50,
                           heatmap_timeout=7, log=lambda *_: None)
    assert row["per_task"]["syn_clean"]["status"] == "ok"
    assert not (tmp_path / "D/public/renders/syn_clean.png").exists()
    err = (tmp_path / "D/private/syn_clean/heatmap_error.txt").read_text(encoding="ascii")
    assert err.startswith("crash") and "boom" in err
    html = (tmp_path / "D/public/report.html").read_text(encoding="ascii")
    assert "renders/syn_clean.png" not in html
    score_call, heat_call = calls
    assert score_call[-1] == "timeout=50" and heat_call[-1] == "timeout=7"
    # the render reuses the score child's alignment
    res_p = heat_call[heat_call.index("--result") + 1]
    assert Path(res_p) == (tmp_path / "D/private/syn_clean/result.json").resolve()


def test_official_layer_submission(gt_env, tmp_path, monkeypatch):
    calls = _fake_children(monkeypatch)
    z = _zip(tmp_path / "s.zip", {"syn_clean/output.step": b"ISO-10303-21;\n"})
    row = score_submission(z, tmp_path / "D", task_ids=["syn_clean"], layer="official",
                           heatmap=False, log=lambda *_: None)
    assert row["layer"] == "official"
    assert re.fullmatch(r"\d+\.\d+\.\d+\+official\.[0-9a-f]{8}", row["eval_version"])
    assert row["eval_version"] == eval_version(EvalConfig(), "official")
    (score_call,) = calls
    assert score_call[score_call.index("--layer") + 1] == "official"
    with pytest.raises(ValueError):
        score_submission(z, tmp_path / "D", task_ids=["syn_clean"], layer="nope")


@pytest.mark.parametrize("bad", ["..", ".", "../x", "a/b", "a\\b", "C:x", "a b", ""])
def test_unsafe_task_ids_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        check_task_ids(["ok_id", bad])
    if bad:
        f = tmp_path / "t.txt"
        f.write_text(f"ok_id\n{bad}\n", encoding="utf-8")
        with pytest.raises(ValueError):
            read_tasks_file(f)
    z = _zip(tmp_path / "s.zip", {})
    out = tmp_path / "D"
    out.mkdir()
    with pytest.raises(ValueError):
        score_submission(z, out, task_ids=["x", bad or ".."], heatmap=False,
                         log=lambda *_: None)
    assert out.is_dir() and z.is_file()
    assert check_task_ids(["r385_hs01_motor_mount", "od.v2-a"]) == ["r385_hs01_motor_mount",
                                                                     "od.v2-a"]


def test_cli_error_names_the_exception_type(capsys, monkeypatch):
    def boom(a):
        raise AssertionError()

    monkeypatch.setattr("recadbench_eval.cli._heatmap", boom)
    p = main(["render-heatmap", "--run-dir", ".", "--part", "p", "--out", "x.png"])
    assert p == 1
    assert capsys.readouterr().err.strip() == "error: AssertionError"


@pytest.mark.skipif(importlib.util.find_spec("matplotlib") is None, reason="no matplotlib")
def test_heatmap_reuses_result_alignment(scored, synth_gt_root, monkeypatch):
    import recadbench_eval.align as align_mod

    def no_icp(*a, **k):
        raise RuntimeError("ICP ran")

    monkeypatch.setenv("RECADBENCH_GT_DIR", str(synth_gt_root))
    monkeypatch.setattr(align_mod, "align", no_icp)
    out, cfg = scored["out"], EvalConfig(**CFG_DICT)
    png = render_heatmap(out / "runs/syn_clean", "syn_clean", cfg, scored["base"] / "reuse.png",
                         result_path=out / "private/syn_clean/result.json")
    assert png.read_bytes()[:4] == b"\x89PNG"
    with pytest.raises(RuntimeError, match="ICP ran"):  # without a result: ICP fallback
        render_heatmap(out / "runs/syn_clean", "syn_clean", cfg, scored["base"] / "icp.png")


@pytest.mark.skipif(os.name != "posix", reason="RLIMIT_AS is set only on POSIX")
def test_posix_child_memory_limit(monkeypatch):
    assert submit_score.run_child(["--help"], 120, sys.executable)[0] == "ok"
    monkeypatch.setattr(submit_score, "MEM_LIMIT_BYTES", 16 * 2**20)
    assert submit_score.run_child(["--help"], 120, sys.executable)[0] == "crash"
