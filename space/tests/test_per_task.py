"""Per-task leaderboard: ranking, filtering, escaping, empty state and the app tab (no network)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SPACE = Path(__file__).resolve().parents[1]
if str(SPACE) not in sys.path:
    sys.path.insert(0, str(SPACE))

import store as st  # noqa: E402

import leaderboard as lb  # noqa: E402

T1, T2 = "r385_hs01_motor_mount", "od_h11_thermoblock"
TASK1 = {"id": T1, "no": 1, "title": "Motor mount cup", "family": "R385 diaphragm pump",
         "difficulty": "medium", "tolerance_class": "B"}  # fmt: skip
LONG_REASON = ("faceted / mesh-like body (too many faces or triangle faces);\n"
               "aligned extents differ too much from the input scan")  # fmt: skip


def _ok(total, s1=30.0, s2=20.0, s3=10.0, s4=5.0):
    return {"status": "ok", "S1": s1, "S2": s2, "S3": s3, "S4": s4, "total": total}


def _row(sid, per_task, name=None, at="2026-11-18T10:00:00Z", **kw):
    return {"submission_id": sid, "status": "done", "submission_name": name or sid,
            "open_total": 1.0, "per_task": per_task, "submitted_at": at, **kw}  # fmt: skip


def _names(rows, tid=T1):
    return [r["submission_name"] for r in lb.task_board_rows(rows, tid)]


def test_ranking_order_and_ties():
    rows = [
        _row("sub-00000001", {T1: _ok(40.0)}, "mid"),
        _row("sub-00000002", {T1: _ok(90.5), T2: _ok(10.0)}, "best"),
        _row("sub-00000003", {T1: _ok(40.0)}, "mid-later", at="2026-11-18T11:00:00Z"),
        _row("sub-00000004", {T1: _ok(None)}, "no-number"),
    ]
    assert _names(rows) == ["best", "mid", "mid-later", "no-number"]
    out = lb.task_board_html(rows, T1, TASK1)
    assert out.index(">best<") < out.index(">mid<") < out.index(">mid-later<")
    assert "rcb-rank r1'>1<" in out and "rcb-rank r3'>3<" in out and "rcb-rank'>4<" in out
    assert ">90.5</b>" in out and "width:95.3%" in out


def test_missing_error_and_unfinished_rows_are_left_out():
    rows = [
        _row("sub-00000001", {T1: _ok(50.0)}, "solved"),
        _row("sub-00000002", {T1: {"status": "missing", "total": 0.0}}, "missing"),
        _row("sub-00000003", {T1: {"status": "error", "total": 0.0}}, "errored"),
        _row("sub-00000004", {T2: _ok(80.0)}, "other-task"),
        _row("sub-00000005", {}, "empty"),
        {"submission_id": "sub-00000006", "status": "pending", "submission_name": "queued",
         "per_task": {T1: _ok(99.0)}},
        {"submission_id": "sub-00000007", "status": "failed", "submission_name": "failed",
         "per_task": {T1: _ok(99.0)}},
        {"submission_id": "sub-00000008", "status": "done", "submission_name": "bad-per-task",
         "per_task": "nope"},
    ]  # fmt: skip
    assert _names(rows) == ["solved"]
    assert _names(rows, T2) == ["other-task"]
    out = lb.task_board_html(rows, T1, TASK1)
    for name in ("missing", "errored", "other-task", "queued", "failed", "bad-per-task"):
        assert f">{name}<" not in out, name


def test_gate_fail_is_listed_last_with_zero():
    rows = [
        _row("sub-00000001", {T1: {"status": "gate_fail", "S1": 30.0, "total": 0.0,
                                   "gate_reason": "not one <solid>"}}, "gated",
             at="2026-11-18T08:00:00Z"),
        _row("sub-00000002", {T1: _ok(0.5)}, "low"),
        _row("sub-00000003", {T1: {"status": "gate_fail", "total": 12.0,
                                   "gate_reason": LONG_REASON}}, "gated-2"),
    ]  # fmt: skip
    assert _names(rows) == ["low", "gated", "gated-2"]
    out = lb.task_board_html(rows, T1, TASK1)
    body = out.split("<tbody>")[1]
    gated = body.split("<tr")[2]
    assert ">0.0</b>" in gated and "rcb-gate no" in gated and "class='unfinished'" in gated
    assert "title='not one &lt;solid&gt;'" in gated and ">30.0<" not in gated
    assert "<summary>why?</summary>not one &lt;solid&gt;</details>" in gated  # no hover needed
    # a long reason (two joined gate sentences, > 80 chars) is kept whole, up to 200 chars
    long_one = " ".join(LONG_REASON.split())
    assert 80 < len(long_one) <= 200
    assert f"title='{long_one}'" in body.split("<tr")[3]
    assert f"</summary>{long_one}</details>" in body.split("<tr")[3]
    assert "rcb-rank'>2<" in gated  # numbered, but no medal for a gate failure
    assert ">12.0<" not in out  # a gate failure always shows 0, whatever total it carries
    assert body.split("<tr")[1].count("rcb-gate ok'>yes<") == 1


def test_hostile_meta_strings_are_escaped_and_links_are_safe():
    rows = [
        _row("sub-00000001", {T1: _ok(50.0)}, "<script>alert(1)</script>",
             submitter_name="<img src=x onerror=alert(1)>", model="m'\"x",
             cad_backend="<b>cad</b>", harness="h & <i>", agent_url="javascript:alert(1)",
             human_in_loop=True),
        _row("sub-00000002", {T1: _ok(60.0)}, "ok", validated=True,
             agent_url="https://ok.example/a?x=1&y='2'"),
        _row("sub-00000003", {T1: _ok(40.0)}, "station", reference=True,
             agent_url="data:text/html,hi"),
    ]  # fmt: skip
    out = lb.task_board_html(rows, T1, TASK1)
    for bad in ("<script", "<img", "<b>cad", "<i>", "javascript:", "data:text", "m'\"x"):
        assert bad not in out, bad
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out and "m&#x27;&quot;x" in out
    assert "&lt;b&gt;cad&lt;/b&gt;" in out and "h &amp; &lt;i&gt;" in out
    assert "href='https://ok.example/a?x=1&amp;y=&#x27;2&#x27;'" in out
    assert "rel='nofollow noopener noreferrer ugc'" in out
    # report links only for scored, non-reference rows, built from the validated id
    assert out.count("class='rcb-report'") == 2
    assert "reports/sub-00000003.html" not in out
    for badge in ("validated", "unvalidated", "reference", "human in loop"):
        assert f">{badge}</span>" in out, badge
    # hostile task metadata is escaped in the header too
    head = lb.task_board_html(
        rows,
        T1,
        {
            "id": T1,
            "title": "<i>x</i>",
            "family": "<b>f",
            "difficulty": "hard'><x",
            "tolerance_class": "<c>",
        },
    )
    assert "<i>x" not in head and "<b>f" not in head and "<c>" not in head and "'><x" not in head


def test_empty_state():
    for rows in ([], [_row("sub-00000001", {T2: _ok(80.0)})]):
        out = lb.task_board_html(rows, T1, TASK1)
        assert "No submission covers this task yet" in out
        assert "Single-task submissions are welcome" in out and "data-tab='Take part'" in out
        assert "<table" not in out and "Motor mount cup" in out


def test_reference_rows_are_ranked_without_report(tmp_path):
    p = tmp_path / "ref.json"
    p.write_text(json.dumps([{"submission_id": "sub-0000000a", "submission_name": "station-a",
                              "per_task": {T1: _ok(70.0)}, "faces_gt": 1}]),
                 encoding="utf-8")  # fmt: skip
    refs = lb.load_reference_rows(p)
    rows = [*refs, _row("sub-00000001", {T1: _ok(50.0)}, "user")]
    assert _names(rows) == ["station-a", "user"]
    out = lb.task_board_html(rows, T1, TASK1)
    assert "reports/sub-0000000a.html" not in out and "reports/sub-00000001.html" in out
    assert ">reference</span>" in out


def test_header_and_columns():
    who = {"submitter_name": "Ada", "model": "mdl", "cad_backend": "build123d", "harness": "hns"}
    rows = [_row("sub-00000001", {T1: _ok(64.0, 31.5, 18.0, 9.5, 5.0)}, "one", **who)]
    out = lb.task_board_html(rows, T1, TASK1, index=0)
    head = out.split("<table")[0]
    parts = (">01<", "Motor mount cup", T1, "R385 diaphragm pump", "d-medium", "class <b>B</b>")
    for part in parts:
        assert part in head, part
    ths = re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", out)
    assert [re.sub(r"<[^>]+>", "", h) for h in ths] == [
        "#", "Submission", "Submitter", "Model", "CAD backend", "Harness", "Task /95",
        "S1 /40", "S2 /25", "S3 /20", "S4 /10", "Gate OK", "",
    ]  # fmt: skip
    cells = re.findall(r"<td[^>]*>(.*?)</td>", out.split("<tbody>")[1])
    text = [re.sub(r"<[^>]+>", "", c) for c in cells]
    assert text[2:6] == ["Ada", "mdl", "build123d", "hns"]
    assert text[6] == "64.0" and text[7:11] == ["31.5", "18.0", "9.5", "5.0"]
    assert text[11] == "yes" and text[12] == "Report"
    # bare task (no metadata): humanized title, family from the id prefix, position number
    bare = lb.task_board_html([], "od_h22_three_way_valve", None, index=6)
    assert ">07<" in bare and "Three way valve" in bare and "Open Dedica" in bare


def test_html_shows_only_public_row_fields():
    secret = {
        "submission_id": "sub-00000001", "status": "done", "submission_name": "pub",
        "submitted_at": "2026-11-18T10:00:00Z", "hf_user": "private-user-zz",
        "job_id": "job-secret-zz", "sha256": "ab" * 32, "status_message": "msg-secret-zz",
        "notes": "note-secret-zz", "gt_total": 77.777,
        "per_task": {T1: {**_ok(50.0), "p_tight": 0.4321, "p_wide": 0.8765, "p95_mm": 0.1234,
                          "key_dims": [{"name": "dim-secret-zz", "ok": True}],
                          "face_ratio_bucket": "0.35-1.5", "gt_volume": 999.999}},
    }  # fmt: skip
    for row in (secret, st.clean_row(secret)):
        out = lb.task_board_html([row], T1, TASK1)
        for leak in ("private-user-zz", "job-secret-zz", "ab" * 32, "msg-secret-zz",
                     "note-secret-zz", "77.777", "0.4321", "0.8765", "0.1234", "dim-secret-zz",
                     "0.35-1.5", "999.999"):  # fmt: skip
            assert leak not in out, leak
        assert ">pub<" in out and ">50.0</b>" in out


# --- app tab ----------------------------------------------------------------------------------


def _local_app(tmp_path, **extra):
    pytest.importorskip("gradio")
    import app as space_app

    env = {"RCB_MODE": "local", "RCB_LOCAL_ROOT": str(tmp_path), "RECADBENCH_GT_DIR": "/gt"}
    store, disp, cfg = space_app.build_backends({**env, **extra})
    return space_app, store, disp, cfg


def test_app_builds_with_per_task_tab(tmp_path):
    space_app, store, disp, cfg = _local_app(tmp_path)
    assert cfg.task_ids[0] == T1  # task no 1, the default of the picker
    store.append_result(_row("sub-0000beef", {T1: _ok(77.0)}, "single-task-run"))
    from fastapi.testclient import TestClient

    client = TestClient(space_app.build_app(store, disp, cfg))
    assert client.get("/").status_code == 200
    config = client.get("/config").json()
    comps = config["components"]
    tabs = [c["props"].get("label") for c in comps if c.get("type") == "tabitem"]
    assert tabs.index("🎯 Per task") == tabs.index("🏆 Leaderboard") + 1
    (drop,) = [c for c in comps if c.get("type") == "dropdown"]
    assert drop["props"]["value"] == T1
    labels = [ch[0] for ch in drop["props"]["choices"]]
    assert len(labels) == len(cfg.task_ids) and labels[0] == "01 · Motor mount cup"
    htmls = [c["props"].get("value") or "" for c in comps if c.get("type") == "html"]
    assert any("single-task-run" in h and "77.0" in h and "rcb-taskboard" in h for h in htmls)
    if cfg.assets_dir is not None:
        (gal,) = [c for c in comps if c.get("type") == "gallery"]
        assert gal["props"]["columns"] == 5 and gal["props"]["allow_preview"] is False
        assert len(gal["props"]["value"]) == len(cfg.task_ids)


def test_gallery_image_sources(tmp_path):
    space_app, _, _, cfg = _local_app(tmp_path)
    inputs = tmp_path / "inputs" / "parts" / T1
    inputs.mkdir(parents=True)
    (inputs / "preview.png").write_bytes(b"\x89PNG")
    assets = tmp_path / "assets" / "tasks"
    assets.mkdir(parents=True)
    (assets / f"{T2}.webp").write_bytes(b"RIFF")
    cfg.inputs_dir, cfg.assets_dir = tmp_path / "inputs", tmp_path / "assets"
    assert space_app._gallery_image(cfg, T1) == str(inputs / "preview.png")
    assert space_app._gallery_image(cfg, T2) == str(assets / f"{T2}.webp")
    assert space_app._gallery_image(cfg, "r385_dr02_wobble_plate") is None
    cfg.inputs_repo = "org/inputs"  # no local copy: the inputs dataset URL (http only)
    url = space_app._gallery_image(cfg, "r385_dr02_wobble_plate")
    assert url.startswith("https://huggingface.co/datasets/org/inputs/resolve/main/parts/")
    assert space_app._gallery_image(cfg, "../x") is None


def test_intro_mentions_single_task_submissions(tmp_path):
    space_app, _, _, cfg = _local_app(tmp_path)
    head = space_app.header_html(cfg)
    assert "Single-task submissions are welcome" in head and "data-tab='Per task'" in head
    page = space_app.take_part_html(cfg)
    assert "Single-task submissions are welcome" in page and "data-tab='Per task'" in page
    single = space_app.zip_layout_single(cfg.task_ids)
    assert lb.esc(single) in page and single.count("/") == 1 and "meta.json" in single
    assert f"{cfg.task_ids[0]}/" in single and "output.step" in single
    assert lb.esc(space_app.zip_layout(cfg.task_ids)) in page  # the full example stays
    assert "Can I submit just one task?" in space_app.faq_html(cfg)


def test_submission_doc_has_single_task_section_and_no_draft_words():
    doc = (SPACE.parent / "docs" / "benchmark" / "submission.md").read_text(encoding="utf-8")
    assert "### Single-task submissions" in doc and "Per task" in doc
    low = doc.lower()
    for word in ("(draft)", "not open yet", "limits (planned)", "coming with the space"):
        assert word not in low, word
