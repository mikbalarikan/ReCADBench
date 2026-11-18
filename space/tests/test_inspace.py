"""In-Space scoring (RCB_MODE=space): queue, scorer env, GT privacy, restart, poller.

No network, no GT, no evaluator run: the Hub API, snapshot_download and the scorer
subprocess are fakes.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

SPACE = Path(__file__).resolve().parents[1]
if str(SPACE) not in sys.path:
    sys.path.insert(0, str(SPACE))

import jobs  # noqa: E402
import runtime  # noqa: E402
import store as st  # noqa: E402
import submit  # noqa: E402

import leaderboard as lb  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
TASKS = ["r385_hs01_motor_mount", "od_h11_thermoblock"]
META = {"submitter_name": "Ada", "submission_name": "agent-x", "model": "m"}
FAKE_TOKEN = "hf_FAKEspaceTOKENforTESTSonly000000000000"
GT_SECRET = b"FAKE-GT-CONTENT-MUST-NOT-BE-SERVED"


def sha_of(i: int) -> str:
    return f"{i:08x}" * 8


def sid_of(i: int) -> str:
    return "sub-" + sha_of(i)[:8]


def fake_inspect(sha: str):
    def _inspect(path, task_ids):
        return SimpleNamespace(
            meta=dict(META), tasks={TASKS[0]: {}}, sha256=sha, missing=[], warnings=[]
        )

    return _inspect


class FakeProc:
    def __init__(self):
        self.rc = None
        self.killed = False

    def poll(self):
        return self.rc

    def kill(self):
        self.killed = True
        self.rc = -9


class FakePopen:
    def __init__(self):
        self.calls: list[SimpleNamespace] = []

    def __call__(self, cmd, env, stdout, stderr, **kw):
        call = SimpleNamespace(cmd=cmd, env=env, kw=kw, proc=FakeProc())
        self.calls.append(call)
        return call.proc

    def zip_of(self, i: int) -> str:
        cmd = self.calls[i].cmd
        return cmd[cmd.index("--zip") + 1]

    def sid_of(self, i: int) -> str:
        cmd = self.calls[i].cmd
        return cmd[cmd.index("--submission-id") + 1]


def finish(disp: jobs.LocalDispatcher, popen: FakePopen, i: int, rc: int = 0, total=42.0):
    sid = popen.sid_of(i)
    out = disp.out_dir(sid)
    if rc == 0:
        (out / "public").mkdir(parents=True, exist_ok=True)
        (out / "public" / "report.html").write_text(f"<p>{sid}</p>", encoding="utf-8")
        (out / "summary.json").write_text(json.dumps({"open_total": total}), encoding="utf-8")
    popen.calls[i].proc.rc = rc


class FakeHfApi:
    """A dataset repo in a local folder (the HubStore calls only)."""

    def __init__(self, root: Path):
        self.root = root
        self.commits = 0

    def file_exists(self, repo_id, filename, repo_type=None, token=None):
        return (self.root / filename).is_file()

    def hf_hub_download(self, repo_id, filename, repo_type=None, token=None, local_dir=None,
                        force_download=False):  # fmt: skip
        dst = Path(local_dir) / filename
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((self.root / filename).read_bytes())
        return str(dst)

    def upload_file(self, path_or_fileobj, path_in_repo, repo_id, repo_type=None, token=None,
                    commit_message=None):  # fmt: skip
        dst = self.root / path_in_repo
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = path_or_fileobj
        if not isinstance(data, bytes):
            data = Path(path_or_fileobj).read_bytes()
        dst.write_bytes(data)
        self.commits += 1

    def upload_folder(self, folder_path, path_in_repo, repo_id, repo_type=None, token=None,
                      commit_message=None):  # fmt: skip
        shutil.copytree(folder_path, self.root / path_in_repo, dirs_exist_ok=True)
        self.commits += 1

    def delete_folder(self, path_in_repo, repo_id, repo_type=None, token=None, commit_message=None):
        shutil.rmtree(self.root / path_in_repo, ignore_errors=True)


def make_queue(tmp_path, store=None, max_parallel=1, gt="gt", popen=None):
    store = store or st.LocalStore(tmp_path / "store")
    popen = popen or FakePopen()
    gt_dir = "" if gt == "" else str(tmp_path / gt)
    disp = jobs.LocalDispatcher(tmp_path / "work", gt_dir=gt_dir, popen=popen, python="py")
    return store, disp, popen, jobs.ScoringQueue(store, disp, max_parallel=max_parallel)


def upload(tmp_path, store, disp, queue, i, user=None, now=NOW):
    zp = tmp_path / f"upload-{i}.zip"  # stands in for the Gradio temp file
    zp.write_bytes(b"PK-zip-%d" % i)
    profile = {"preferred_username": user or f"user{i}"}
    text = submit.handle_submission(
        zp, profile, store, disp, now, task_ids=TASKS, inspect=fake_inspect(sha_of(i)),
        queue=queue,
    )  # fmt: skip
    return text, zp


# --- scorer environment -----------------------------------------------------------------------


def test_scorer_child_never_gets_tokens_or_oauth_vars(tmp_path, monkeypatch):
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_HUB_TOKEN", "HF_API_TOKEN"):
        monkeypatch.setenv(k, FAKE_TOKEN)
    for k in ("OAUTH_CLIENT_ID", "OAUTH_CLIENT_SECRET", "OAUTH_SCOPES", "OAUTH_X"):
        monkeypatch.setenv(k, "oauth-value")
    monkeypatch.setenv("RCB_KEEP_ME", "1")
    _, disp, popen, _ = make_queue(tmp_path)
    disp.dispatch(sid_of(1), sha_of(1), str(tmp_path / "z.zip"))
    env = popen.calls[0].env
    assert not [k for k in env if "TOKEN" in k.upper() or k.upper().startswith("OAUTH_")]
    assert FAKE_TOKEN not in json.dumps(env)
    assert env["RCB_KEEP_ME"] == "1" and env["RECADBENCH_GT_DIR"] == str(tmp_path / "gt")
    import os

    assert os.environ["HF_TOKEN"] == FAKE_TOKEN  # the app itself keeps its token
    assert jobs.scrubbed_env({"PATH": "p", "hf_token": "x", "OAuth_Y": "y"}) == {"PATH": "p"}


# --- FIFO queue -------------------------------------------------------------------------------


def test_queue_runs_one_at_a_time_fifo_from_a_local_copy(tmp_path):
    store, disp, popen, queue = make_queue(tmp_path)
    texts = []
    for i in (1, 2, 3):
        text, zp = upload(tmp_path, store, disp, queue, i, now=NOW + timedelta(seconds=i))
        zp.unlink()  # Gradio may drop its temp file; the scorer reads the staged copy
        texts.append(text)
    assert "scoring has started" in texts[0]
    assert "1 run(s) ahead" in texts[1] and "2 run(s) ahead" in texts[2]
    assert "one at a time" in texts[1]
    assert len(popen.calls) == 1
    staged = Path(popen.zip_of(0))
    assert staged == tmp_path / "work" / "zips" / f"{sha_of(1)}.zip" and staged.is_file()
    rows = {r["submission_id"]: r for r in store.list_results()}
    assert rows[sid_of(1)]["status"] == "scoring" and rows[sid_of(1)]["job_id"]
    assert [r["submission_id"] for r in st.waiting_rows(store.list_results())] == [
        sid_of(2), sid_of(3)
    ]  # fmt: skip
    assert queue.position(sid_of(3)) == 2
    html = lb.my_submissions_html(store.list_results(), "user3")
    assert "#2 in line" in html

    queue.tick()  # still running: nothing new starts
    assert len(popen.calls) == 1
    finish(disp, popen, 0, total=50.0)
    queue.tick()
    rows = {r["submission_id"]: r for r in store.list_results()}
    assert rows[sid_of(1)]["status"] == "done" and rows[sid_of(1)]["open_total"] == 50.0
    assert store.get_report_html(sid_of(1)) == f"<p>{sid_of(1)}</p>"
    assert rows[sid_of(2)]["status"] == "scoring" and rows[sid_of(3)]["status"] == "pending"
    assert popen.sid_of(1) == sid_of(2) and Path(popen.zip_of(1)).is_file()

    finish(disp, popen, 1, rc=1)  # a crashed scorer fails its row, the queue moves on
    queue.tick()
    rows = {r["submission_id"]: r for r in store.list_results()}
    assert rows[sid_of(2)]["status"] == "failed"
    assert rows[sid_of(3)]["status"] == "scoring" and popen.sid_of(2) == sid_of(3)


def test_queue_max_parallel_two(tmp_path):
    store, disp, popen, queue = make_queue(tmp_path, max_parallel=2)
    for i in (1, 2, 3):
        upload(tmp_path, store, disp, queue, i, now=NOW + timedelta(seconds=i))
    assert [popen.sid_of(i) for i in range(len(popen.calls))] == [sid_of(1), sid_of(2)]
    assert disp.active() == 2
    finish(disp, popen, 1)
    queue.tick()
    assert popen.sid_of(2) == sid_of(3)


def test_dedupe_and_tick_lock_never_double_dispatch(tmp_path):
    store, disp, popen, queue = make_queue(tmp_path)
    upload(tmp_path, store, disp, queue, 1)
    text, _ = upload(tmp_path, store, disp, queue, 1, user="user1")
    assert "already submitted" in text
    upload(tmp_path, store, disp, queue, 2)
    threads = [threading.Thread(target=queue.tick) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(popen.calls) == 1
    got = []
    with queue._lock:  # the poller is ticking: a page event skips instead of waiting
        t = threading.Thread(target=lambda: got.append(queue.tick(blocking=False)))
        t.start()
        t.join(5)
    assert got == [False] and queue.tick(blocking=False) is True


def test_not_configured_accepts_uploads_and_keeps_them_pending(tmp_path):
    api = FakeHfApi(tmp_path / "repo")
    hub = st.HubStore("o/subs", FAKE_TOKEN, cache_dir=tmp_path / "c", api=api)
    store, disp, popen, queue = make_queue(tmp_path, store=hub, gt="")
    assert not disp.configured
    text, _ = upload(tmp_path, store, disp, queue, 1)
    assert "not configured" in text and not text.startswith("Error")
    row = store.get_result(sid_of(1))
    assert row["status"] == "pending" and row["status_message"] == jobs.NOT_CONFIGURED
    assert (tmp_path / "repo" / "submissions" / f"{sha_of(1)}.zip").is_file()
    commits = api.commits
    for _ in range(3):
        queue.tick()
    assert api.commits == commits and popen.calls == []  # no write storm, no scorer
    assert "scoring not configured" in lb.my_submissions_html(store.list_results(), "user1")


# --- restart ----------------------------------------------------------------------------------


def test_restart_requeues_waiting_rows_and_fails_lost_runs(tmp_path):
    api = FakeHfApi(tmp_path / "repo")
    hub = st.HubStore("o/subs", FAKE_TOKEN, cache_dir=tmp_path / "c1", api=api)
    store, disp, popen, queue = make_queue(tmp_path / "before", store=hub)
    old = NOW - timedelta(hours=5)
    for i in (1, 2, 3):
        upload(tmp_path, store, disp, queue, i, now=old + timedelta(seconds=i))
    finish(disp, popen, 0, total=30.0)  # run 1 finished right before the restart
    assert [popen.sid_of(i) for i in range(len(popen.calls))] == [sid_of(1)]
    store.update_result(sid_of(2), status="scoring", job_id=f"local-{sid_of(2)}")  # in flight

    # restart: new process, empty work folder (container), same Hub repo
    shutil.rmtree(tmp_path / "before" / "work" / "zips")
    hub2 = st.HubStore("o/subs", FAKE_TOKEN, cache_dir=tmp_path / "c2", api=api)
    popen2 = FakePopen()
    disp2 = jobs.LocalDispatcher(tmp_path / "before" / "work", gt_dir="gt", popen=popen2)
    queue2 = jobs.ScoringQueue(hub2, disp2)
    lost = queue2.recover()
    assert lost == [sid_of(2)]
    rows = {r["submission_id"]: r for r in hub2.list_results()}
    assert rows[sid_of(1)]["status"] == "done" and rows[sid_of(1)]["open_total"] == 30.0
    assert rows[sid_of(2)]["status"] == "failed"
    assert rows[sid_of(2)]["status_message"] == jobs.INTERRUPTED
    swept = submit.sweep_pending(hub2, disp2, now=NOW, keep_waiting=True)
    assert swept == [] and hub2.get_result(sid_of(3))["status"] == "pending"
    queue2.pump()  # the waiting row is re-queued: zip fetched back from the Hub repo
    assert popen2.sid_of(0) == sid_of(3) and Path(popen2.zip_of(0)).read_bytes() == b"PK-zip-3"
    # hub mode (no queue) keeps the old rule: an old row without a job is stale
    hub2.update_result(sid_of(3), status="pending", job_id="")
    assert submit.sweep_pending(hub2, None, now=NOW) == [sid_of(3)]


def test_start_scoring_recovers_sweeps_and_polls(tmp_path, monkeypatch):
    pytest.importorskip("gradio")
    import app as space_app

    env = {"RCB_MODE": "local", "RCB_LOCAL_ROOT": str(tmp_path), "RECADBENCH_GT_DIR": "/gt"}
    store, disp, cfg = space_app.build_backends(env)
    popen = FakePopen()
    disp._popen = popen
    store.append_result({"submission_id": sid_of(9), "sha256": sha_of(9), "status": "pending",
                         "job_id": "", "queued_at": "2020-01-01T00:00:00Z"})  # fmt: skip
    (tmp_path / "store" / "submissions").mkdir(parents=True)
    (tmp_path / "store" / "submissions" / f"{sha_of(9)}.zip").write_bytes(b"PK")
    queue, poller = space_app.start_scoring(store, disp, cfg, every_s=0.05)
    try:
        deadline = time.time() + 5
        while not popen.calls and time.time() < deadline:
            time.sleep(0.02)
        assert popen.sid_of(0) == sid_of(9)  # old waiting row: re-queued by the poller
        finish(disp, popen, 0, total=12.5)
        deadline = time.time() + 5
        while store.get_result(sid_of(9))["status"] != "done" and time.time() < deadline:
            time.sleep(0.02)
        assert store.get_result(sid_of(9))["open_total"] == 12.5
    finally:
        poller.stop()


def test_poller_survives_errors_and_stops():
    n = {"ticks": 0}

    def tick():
        n["ticks"] += 1
        if n["ticks"] == 1:
            raise RuntimeError("network blip")

    p = runtime.Poller(tick, every_s=0.01).start()
    deadline = time.time() + 5
    while n["ticks"] < 3 and time.time() < deadline:
        time.sleep(0.01)
    p.stop()
    assert n["ticks"] >= 3 and not p._thread.is_alive()


def test_watchdog_kills_a_hung_scorer(tmp_path):
    now = [0.0]
    popen = FakePopen()
    disp = jobs.LocalDispatcher(tmp_path, gt_dir="gt", popen=popen, max_run_s=100,
                                clock=lambda: now[0])  # fmt: skip
    job = disp.dispatch(sid_of(1), sha_of(1), "z.zip")
    assert disp.status(job) == jobs.RUNNING
    now[0] = 101
    assert disp.status(job) == jobs.ERROR and popen.calls[0].proc.killed
    assert disp.active() == 0
    assert jobs.LocalDispatcher(tmp_path, timeout_s=900).max_run_s > 10 * 2 * 900


def test_admin_rescore_goes_through_the_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("RCB_ADMINS", "root")
    store, disp, popen, queue = make_queue(tmp_path)
    upload(tmp_path, store, disp, queue, 1)
    admin = {"preferred_username": "root"}
    msg = submit.admin_action("rescore", sid_of(1), admin, store, disp, queue=queue)
    assert msg.startswith("Error") and len(popen.calls) == 1  # still running
    finish(disp, popen, 0)
    queue.tick()
    msg = submit.admin_action("rescore", sid_of(1), admin, store, disp, NOW, queue=queue)
    assert "queued for rescoring" in msg and len(popen.calls) == 2
    assert store.get_result(sid_of(1))["status"] == "scoring"


# --- space-mode boot --------------------------------------------------------------------------


def fake_gt_download(calls: list):
    def download(repo_id, repo_type, token, local_dir):
        calls.append({"repo_id": repo_id, "repo_type": repo_type, "token": token,
                      "local_dir": local_dir})  # fmt: skip
        part = Path(local_dir) / "parts" / TASKS[0]
        part.mkdir(parents=True, exist_ok=True)
        (part / "gt.step").write_bytes(GT_SECRET)
        return local_dir

    return download


def space_env(tmp_path, **extra):
    env = {
        "RCB_MODE": "space",
        "HOME": str(tmp_path / "home"),
        "HF_TOKEN": FAKE_TOKEN,
        "RCB_SUBMISSIONS_REPO": "o/subs",
        "RCB_GT_REPO": "o/gt",
        "RCB_MAX_PARALLEL": "1",
    }
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


def test_space_mode_boots_hubstore_local_scorer_and_private_gt(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app

    calls: list = []
    api = FakeHfApi(tmp_path / "repo")
    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=api, gt_download=fake_gt_download(calls)
    )
    home = (tmp_path / "home").resolve()
    assert isinstance(store, st.HubStore) and store.api is api
    assert isinstance(disp, jobs.LocalDispatcher) and disp.configured
    assert calls == [{"repo_id": "o/gt", "repo_type": "dataset", "token": FAKE_TOKEN,
                      "local_dir": str(home / ".rcb-gt")}]  # fmt: skip
    assert Path(disp.gt_dir) == home / ".rcb-gt" / "parts"
    assert Path(disp.work_root).is_relative_to(home / ".rcb-work")
    assert {home / ".rcb-gt", home / ".rcb-work"} <= set(cfg.private_dirs)
    assert cfg.max_parallel == 1 and cfg.mode == "space" and not cfg.oauth
    roots = runtime.served_roots(space_env(tmp_path), space_app.HERE)
    assert runtime.is_private(disp.gt_dir, roots)
    assert space_app.make_queue(store, disp, cfg).max_parallel == 1


def test_space_mode_starts_without_token_or_gt(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app
    from fastapi.testclient import TestClient

    def broken(**kw):
        raise OSError("401 from the hub")

    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=FakeHfApi(tmp_path / "repo"), gt_download=broken
    )
    assert isinstance(store, st.HubStore) and not disp.configured
    store, disp, cfg = space_app.build_backends(space_env(tmp_path, HF_TOKEN=None))
    assert isinstance(store, st.LocalStore) and not disp.configured
    queue = space_app.make_queue(store, disp, cfg)
    text, _ = upload(tmp_path, store, disp, queue, 1)
    assert "not configured" in text
    client = TestClient(space_app.build_app(store, disp, cfg, queue=queue))
    assert client.get("/").status_code == 200


def test_private_dir_never_lands_in_a_served_folder(tmp_path):
    home = tmp_path / "home"
    app_dir = home / "app"
    app_dir.mkdir(parents=True)
    env = {"HOME": str(home), "GRADIO_TEMP_DIR": str(tmp_path / "gtmp")}
    # the working directory is $HOME itself: $HOME/.rcb-gt would sit inside it
    roots = runtime.served_roots(env, app_dir, cwd=home)
    got = runtime.private_dir(home / runtime.GT_DIRNAME, roots, "rcb-gt-")
    try:
        assert not got.is_relative_to(home.resolve()) and runtime.is_private(got, roots)
    finally:
        shutil.rmtree(got, ignore_errors=True)
    roots = runtime.served_roots(env, app_dir, cwd=app_dir)
    assert runtime.private_dir(home / ".rcb-gt", roots, "x") == (home / ".rcb-gt").resolve()
    assert not runtime.is_private(tmp_path / "gtmp" / "x", roots)
    anchor = Path(Path.cwd().anchor)  # a "/" working directory is skipped, not a served root
    assert anchor not in runtime.served_roots(env, app_dir, cwd=anchor)


def test_gradio_and_routes_never_serve_gt_or_scorer_files(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app
    from fastapi.testclient import TestClient

    api = FakeHfApi(tmp_path / "repo")
    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=api, gt_download=fake_gt_download([])
    )
    gt_file = Path(disp.gt_dir) / TASKS[0] / "gt.step"
    assert gt_file.read_bytes() == GT_SECRET
    private = disp.out_dir(sid_of(1)) / "private" / "result.json"
    private.parent.mkdir(parents=True)
    private.write_bytes(GT_SECRET)
    client = TestClient(space_app.build_app(store, disp, cfg))
    assert client.get("/").status_code == 200
    urls = []
    for f in (gt_file, private):
        for s in (str(f), f.as_posix()):
            for prefix in ("/gradio_api/file=", "/file="):
                urls += [prefix + s, prefix + quote(s)]
    urls += [
        f"/assets/../../home/.rcb-gt/parts/{TASKS[0]}/gt.step",
        f"/assets/..%2F..%2Fhome%2F.rcb-gt%2Fparts%2F{TASKS[0]}%2Fgt.step",
        f"/previews/{TASKS[0]}.png",
        f"/reports/{sid_of(1)}/private/result.json",
        f"/reports/{sid_of(1)}/..%2F..%2F.rcb-gt%2Fparts%2F{TASKS[0]}%2Fgt.step",
    ]
    for url in urls:
        r = client.get(url)
        assert r.status_code in (403, 404), (url, r.status_code)
        assert GT_SECRET not in r.content, url


def test_harden_process_is_best_effort(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "darwin")  # never change the test process
    assert runtime.harden_process() is False
    assert runtime.max_parallel({"RCB_MAX_PARALLEL": "3"}) == 3
    assert runtime.max_parallel({"RCB_MAX_PARALLEL": "zero"}) == 1
    assert runtime.max_parallel({}) == 1


# --- review fixes: boot resilience, fairness, isolation, cleanup, OAuth ------------------------


class FlakyHfApi(FakeHfApi):
    """The Hub is unreachable while ``broken`` (timeouts, 5xx, 429 are all re-raised)."""

    broken = False

    def file_exists(self, *a, **kw):
        if self.broken:
            raise ConnectionError("hub down")
        return super().file_exists(*a, **kw)


def _zip(tmp_path: Path, i: int) -> Path:
    p = tmp_path / f"seed-{i}.zip"
    p.write_bytes(b"PK-zip-%d" % i)
    return p


def test_start_scoring_survives_a_hub_error_at_boot_and_recovers_later(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app

    api = FlakyHfApi(tmp_path / "repo")
    seed = st.HubStore("o/subs", FAKE_TOKEN, cache_dir=tmp_path / "c0", api=api)
    seed.append_result(
        {
            "submission_id": sid_of(1),
            "sha256": sha_of(1),
            "status": "scoring",
            "job_id": f"local-{sid_of(1)}",
            "queued_at": "2026-11-18T11:00:00Z",
        }
    )
    seed.append_result({"submission_id": sid_of(2), "sha256": sha_of(2), "status": "pending",
                        "job_id": "", "queued_at": "2026-11-18T11:01:00Z"})  # fmt: skip
    seed.put_zip(sha_of(2), _zip(tmp_path, 2))
    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=api, gt_download=fake_gt_download([])
    )
    store.cache_ttl_s = 0
    popen = FakePopen()
    disp._popen = popen
    api.broken = True
    queue, poller = space_app.start_scoring(store, disp, cfg, every_s=0.02)  # must not raise
    try:
        assert queue is not None and not queue.recovered
        time.sleep(0.1)
        assert popen.calls == []  # nothing dispatched before the recovery succeeded
        api.broken = False
        deadline = time.time() + 5
        while not popen.calls and time.time() < deadline:
            time.sleep(0.02)
        assert queue.recovered and popen.sid_of(0) == sid_of(2)
        row = store.get_result(sid_of(1))
        assert row["status"] == "failed" and row["status_message"] == jobs.INTERRUPTED
    finally:
        poller.stop()


def test_build_app_starts_when_the_hub_is_down(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app
    from fastapi.testclient import TestClient

    api = FlakyHfApi(tmp_path / "repo")
    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=api, gt_download=fake_gt_download([])
    )
    api.broken = True
    client = TestClient(space_app.build_app(store, disp, cfg))
    assert client.get("/").status_code == 200


def test_one_open_submission_per_user_and_a_bounded_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("RCB_ADMINS", "root")
    store, disp, popen, queue = make_queue(tmp_path)
    text, _ = upload(tmp_path, store, disp, queue, 1, user="ada")
    assert "scoring has started" in text
    text, _ = upload(tmp_path, store, disp, queue, 2, user="ada")
    assert text.startswith("Error") and "already have a submission" in text
    assert store.get_result(sid_of(2)) is None
    text, _ = upload(tmp_path, store, disp, queue, 3, user="root")  # admins are not capped
    assert not text.startswith("Error")
    text, _ = upload(tmp_path, store, disp, queue, 4, user="root")
    assert not text.startswith("Error")
    finish(disp, popen, 0)
    queue.tick()
    text, _ = upload(tmp_path, store, disp, queue, 2, user="ada")  # the first one finished
    assert not text.startswith("Error")

    monkeypatch.setattr(submit, "MAX_WAITING", 2)
    assert len(st.waiting_rows(store.list_results())) == 2
    text, _ = upload(tmp_path, store, disp, queue, 5, user="bob")
    assert text == "Error: the scoring queue is full; please try again later."


def test_scorer_runs_isolated_in_its_own_folder_and_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    popen = FakePopen()
    disp = jobs.LocalDispatcher("work", gt_dir="gt", popen=popen, python="py",
                                tasks_file="tasks.txt")  # fmt: skip
    disp.dispatch(sid_of(1), sha_of(1), "z.zip")
    call = popen.calls[0]
    out = tmp_path / "work" / sid_of(1)
    assert call.kw == {"cwd": str(out), "start_new_session": True}
    assert call.cmd[:3] == ["py", "-I", "-m"]
    # the child starts in its output folder: every path it gets is absolute
    for flag, want in (("--zip", tmp_path / "z.zip"), ("--out-dir", out),
                       ("--tasks-file", tmp_path / "tasks.txt")):  # fmt: skip
        assert Path(call.cmd[call.cmd.index(flag) + 1]) == want
    assert Path(call.env["RECADBENCH_GT_DIR"]) == tmp_path / "gt"


def test_watchdog_kills_the_scorer_process_group_and_forgets_the_handle(tmp_path, monkeypatch):
    killed = []
    fake_os = SimpleNamespace(name="posix", killpg=lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(jobs, "os", fake_os)

    class Proc(FakeProc):
        pid = 4242
        waited = False

        def wait(self, timeout=None):
            self.waited = True

    proc = Proc()
    jobs.kill_tree(proc)
    assert killed == [(4242, 9)] and proc.killed and proc.waited
    monkeypatch.undo()

    now = [0.0]
    popen = FakePopen()
    disp = jobs.LocalDispatcher(tmp_path, gt_dir="gt", popen=popen, max_run_s=10,
                                clock=lambda: now[0])  # fmt: skip
    job = disp.dispatch(sid_of(1), sha_of(1), "z.zip")
    now[0] = 11
    assert disp.status(job) == jobs.ERROR and popen.calls[0].proc.killed
    assert disp._procs == {} and disp._started == {}  # pruned; the outcome is kept
    assert disp.status(job) == jobs.ERROR and disp.active() == 0


def test_collect_drops_the_extracted_steps(tmp_path):
    store, disp, popen, queue = make_queue(tmp_path)
    upload(tmp_path, store, disp, queue, 1)
    step = disp.out_dir(sid_of(1)) / "runs" / TASKS[0] / "out" / "output.step"
    step.parent.mkdir(parents=True)
    step.write_bytes(b"ISO-10303-21;")
    finish(disp, popen, 0)
    queue.tick()
    assert store.get_result(sid_of(1))["status"] == "done"
    assert not step.parent.exists() and disp.out_dir(sid_of(1)).joinpath("summary.json").is_file()
    assert disp._procs == {}


def test_accepted_upload_drops_the_gradio_temp_copy(tmp_path, monkeypatch):
    pytest.importorskip("gradio")
    import app as space_app

    gtmp = tmp_path / "gradio-tmp"
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(gtmp))
    inside = gtmp / "abc" / "submission.zip"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"PK")
    outside = tmp_path / "keep.zip"
    outside.write_bytes(b"PK")
    assert space_app.drop_upload(inside) and not inside.exists()
    assert not space_app.drop_upload(outside) and outside.exists()
    assert not space_app.drop_upload(None)
    store, disp, cfg = space_app.build_backends(
        {"RCB_MODE": "local", "RCB_LOCAL_ROOT": str(tmp_path / "root")}
    )
    demo = space_app.build_demo(store, disp, cfg)
    assert demo.delete_cache == space_app.GRADIO_DELETE_CACHE == (1800, 3600)


def test_space_oauth_is_never_mocked(tmp_path, monkeypatch):
    pytest.importorskip("gradio")
    import os

    import app as space_app
    import gradio.oauth as go

    cfg = SimpleNamespace(oauth=True)
    environ = {"SPACE_ID": "o/recadbench-open"}
    assert space_app.ensure_space_oauth(cfg, environ) and environ["SYSTEM"] == "spaces"
    assert not space_app.ensure_space_oauth(cfg, environ)  # already set
    assert not space_app.ensure_space_oauth(SimpleNamespace(oauth=False), {"SPACE_ID": "x"})
    assert not space_app.ensure_space_oauth(cfg, {})  # local dev: Gradio's mock is wanted

    # a Space whose image forgot SYSTEM=spaces: the app still installs the REAL login routes
    monkeypatch.setenv("SYSTEM", "unset-me")
    monkeypatch.delenv("SYSTEM")  # removed again after the test
    monkeypatch.setenv("SPACE_ID", "o/recadbench-open")
    for k, v in (("OAUTH_CLIENT_ID", "cid"), ("OAUTH_CLIENT_SECRET", "secret"),
                 ("OAUTH_SCOPES", "openid profile"),
                 ("OPENID_PROVIDER_URL", "https://hub.invalid")):  # fmt: skip
        monkeypatch.setattr(go, k, v)
    # if the guard regressed, the mock must not reach the Hub with this machine's login
    monkeypatch.setattr(go, "_get_mocked_oauth_info", lambda: {"userinfo": {}})
    store, disp, cfg = space_app.build_backends(
        {"RCB_MODE": "local", "RCB_LOCAL_ROOT": str(tmp_path), "SPACE_ID": "o/recadbench-open"}
    )
    assert cfg.oauth
    app = space_app.build_app(store, disp, cfg)
    assert os.environ["SYSTEM"] == "spaces"

    def endpoints(routes):
        for r in routes:
            yield from endpoints(getattr(getattr(r, "app", None), "routes", None) or [])
            if getattr(r, "path", "") == "/login/callback":
                yield r.endpoint.__qualname__

    names = list(endpoints(app.routes))
    assert names and not any("mocked" in n for n in names), names


def test_task_timeout_knob():
    assert runtime.task_timeout_s({}) == 900
    assert runtime.task_timeout_s({"RCB_TASK_TIMEOUT": "300"}) == 300
    assert runtime.task_timeout_s({"RCB_TASK_TIMEOUT": "5"}) == runtime.MIN_TASK_TIMEOUT_S
    assert runtime.task_timeout_s({"RCB_TASK_TIMEOUT": "x"}) == 900


# --- review round 3: PID 1 isolation, run cap, lost children, GT retry, quiet page events -------


def test_isolation_problem_refuses_a_readable_foreign_pid1_environment(tmp_path):
    env1 = tmp_path / "environ"
    env1.write_bytes(b"PATH=/usr/bin\0HF_TOKEN=" + FAKE_TOKEN.encode() + b"\0HOME=/home/user\0")
    kw = {"platform": "linux", "pid1_environ": env1}
    why = runtime.isolation_problem(True, pid=7, **kw)
    assert why and "PID 1" in why and FAKE_TOKEN not in why
    env1.write_bytes(b"PATH=/usr/bin\0OAUTH_CLIENT_SECRET=s\0")
    assert runtime.isolation_problem(True, pid=7, **kw)
    assert runtime.isolation_problem(True, pid=1, **kw) is None  # the app itself is PID 1
    assert "non-dumpable" in runtime.isolation_problem(False, pid=1, **kw)
    env1.write_bytes(b"PATH=/usr/bin\0RCB_MODE=space\0")
    assert runtime.isolation_problem(True, pid=7, **kw) is None
    missing = {"platform": "linux", "pid1_environ": tmp_path / "nope"}  # unreadable: no leak
    assert runtime.isolation_problem(True, pid=7, **missing) is None
    assert runtime.isolation_problem(False, platform="win32") is None


def test_disable_scoring_keeps_uploads_pending(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app

    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=FakeHfApi(tmp_path / "repo"),
        gt_download=fake_gt_download([]),
    )  # fmt: skip
    assert disp.configured and cfg.gt_retry is not None
    space_app.disable_scoring(disp, cfg, "test")
    assert not disp.configured and cfg.gt_retry is None


def test_space_run_watchdog_is_capped():
    assert runtime.max_run_s({}, 900) == runtime.DEFAULT_MAX_RUN_S == 2700
    assert runtime.max_run_s({"RCB_MAX_RUN": "3600"}, 900) == 3600
    assert runtime.max_run_s({"RCB_MAX_RUN": "99999"}, 60) == 10 * 2 * 60 + 300
    assert runtime.max_run_s({"RCB_MAX_RUN": "5"}, 900) == runtime.MIN_MAX_RUN_S
    assert runtime.max_run_s({"RCB_MAX_RUN": "x"}, 900) == 2700


def test_space_backends_use_the_capped_watchdog(tmp_path):
    setup = runtime.space_backends(
        space_env(tmp_path, RCB_TASK_TIMEOUT="420"), app_dir=tmp_path / "app",
        cwd=tmp_path / "app", hub_api=FakeHfApi(tmp_path / "repo"),
        gt_download=fake_gt_download([]),
    )  # fmt: skip
    assert setup.dispatcher.timeout_s == 420 and setup.dispatcher.max_run_s == 2700


def test_admin_delete_of_a_running_row_frees_the_scorer_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("RCB_ADMINS", "boss")
    store, disp, popen, queue = make_queue(tmp_path)
    upload(tmp_path, store, disp, queue, 1)
    upload(tmp_path, store, disp, queue, 2)
    assert len(popen.calls) == 1 and disp.active() == 1
    msg = submit.admin_action("delete", sid_of(1), {"username": "boss"}, store, disp, queue=queue)
    assert not msg.startswith("Error")
    queue.tick()  # the lost child is killed and forgotten; the next row starts
    assert popen.calls[0].proc.killed and f"local-{sid_of(1)}" not in disp._procs
    assert len(popen.calls) == 2 and popen.sid_of(1) == sid_of(2)
    assert store.get_result(sid_of(2))["status"] == "scoring" and disp.active() == 1


def test_reap_kills_children_past_the_watchdog_and_rows_that_failed(tmp_path):
    now = [0.0]
    popen = FakePopen()
    disp = jobs.LocalDispatcher(tmp_path, gt_dir="gt", popen=popen, max_run_s=100,
                                clock=lambda: now[0])  # fmt: skip
    a = disp.dispatch(sid_of(1), sha_of(1), "a.zip")
    b = disp.dispatch(sid_of(2), sha_of(2), "b.zip")
    c = disp.dispatch(sid_of(3), sha_of(3), "c.zip")
    assert disp.reap({a, b, c}) == []
    assert disp.reap({a, c}) == [b] and popen.calls[1].proc.killed  # its row failed
    popen.calls[2].proc.rc = 0  # finished, not yet polled: left for status()
    now[0] = 101
    assert disp.reap({a, c}) == [a] and disp.status(c) == jobs.ERROR  # no summary written
    assert disp._procs == {} and disp.active() == 0


def test_reap_group_only_as_pid1_and_never_waits_for_any_child(monkeypatch):
    calls = []

    def waitpid(pid, flags):
        calls.append(pid)
        if len(calls) == 1:
            return (4243, 0)  # one exited member reaped
        if len(calls) == 2:
            return (0, 0)  # others still running
        raise ChildProcessError

    fake_os = SimpleNamespace(name="posix", getpid=lambda: 1, waitpid=waitpid, WNOHANG=1)
    monkeypatch.setattr(jobs, "os", fake_os)
    assert jobs.reap_group(4242) is False and calls == [-4242, -4242]
    assert jobs.reap_group(4242) is True and calls == [-4242, -4242, -4242]
    fake_os.getpid = lambda: 77  # not PID 1: the real init reaps orphans
    assert jobs.reap_group(4242) is True and len(calls) == 3


def test_finished_groups_are_reaped_on_later_ticks(tmp_path, monkeypatch):
    popen = FakePopen()
    disp = jobs.LocalDispatcher(tmp_path, gt_dir="gt", popen=popen)
    job = disp.dispatch(sid_of(1), sha_of(1), "a.zip")
    popen.calls[0].proc.pid = 4242
    popen.calls[0].proc.rc = 1
    assert disp.status(job) == jobs.ERROR and disp._groups == {4242}
    pending = [False, True]
    monkeypatch.setattr(jobs, "reap_group", lambda pgid: pending.pop(0))
    disp.reap(set())
    assert disp._groups == {4242}  # members still running
    disp.reap(set())
    assert disp._groups == set()


def _flaky_gt(calls: list, state: dict):
    ok = fake_gt_download(calls)

    def download(**kw):
        if state["fail"]:
            raise OSError("503")
        return ok(**kw)

    return download


def test_gt_retry_enables_scoring_after_a_failed_boot_download(tmp_path):
    now = [0.0]
    calls: list = []
    state = {"fail": True}
    setup = runtime.space_backends(
        space_env(tmp_path), app_dir=tmp_path / "app", cwd=tmp_path / "app",
        hub_api=FakeHfApi(tmp_path / "repo"), gt_download=_flaky_gt(calls, state),
    )  # fmt: skip
    disp, retry = setup.dispatcher, setup.gt_retry
    assert not disp.configured and retry is not None
    retry._clock = lambda: now[0]
    retry._last = 0.0
    assert retry(disp) is False  # too soon after the boot attempt
    now[0] = runtime.GT_RETRY_EVERY_S + 1
    assert retry(disp) is False and not disp.configured  # still failing
    state["fail"] = False
    now[0] += runtime.GT_RETRY_EVERY_S / 2
    assert retry(disp) is False and not calls  # back-off
    now[0] += runtime.GT_RETRY_EVERY_S
    assert retry(disp) is True and disp.configured
    assert Path(disp.gt_dir) == Path(calls[0]["local_dir"]) / "parts"
    assert retry(disp) is False and len(calls) == 1  # configured: never again


def test_poller_retries_gt_and_then_scores_waiting_rows(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app

    state = {"fail": True}
    api = FakeHfApi(tmp_path / "repo")
    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=api, gt_download=_flaky_gt([], state)
    )
    store.cache_ttl_s = 0
    popen = FakePopen()
    disp._popen = popen
    queue = space_app.make_queue(store, disp, cfg)
    text, _ = upload(tmp_path, store, disp, queue, 1)
    assert "not configured" in text and not popen.calls
    cfg.gt_retry.every_s = 0.0
    state["fail"] = False
    _queue, poller = space_app.start_scoring(store, disp, cfg, every_s=0.02)
    try:
        deadline = time.time() + 5
        while not popen.calls and time.time() < deadline:
            time.sleep(0.02)
        assert disp.configured and popen.sid_of(0) == sid_of(1)
        assert store.get_result(sid_of(1))["status"] == "scoring"
    finally:
        poller.stop()


def test_page_events_do_no_queue_work_while_the_poller_runs(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app

    store, disp, popen, queue = make_queue(tmp_path)
    ticks = []
    queue.tick = lambda blocking=True: ticks.append(blocking) or True
    refresh = space_app._Refresher(store, disp, queue=queue)
    refresh(force=True)
    assert ticks == [False]  # no poller (tests, a bare build_app): a non-blocking tick
    queue.polled = True
    refresh(force=True)
    refresh()
    assert ticks == [False]


class Hub401Error(OSError):
    """Stands in for huggingface_hub's HfHubHTTPError (an OSError) on a rejected token."""


class RejectingHfApi(FakeHfApi):
    def file_exists(self, *a, **kw):
        raise Hub401Error("401 Invalid credentials in Authorization header")


def test_space_boots_and_serves_with_a_token_the_hub_rejects(tmp_path):
    pytest.importorskip("gradio")
    import app as space_app
    from fastapi.testclient import TestClient

    def gt_401(**kw):
        raise Hub401Error("401")

    store, disp, cfg = space_app.build_backends(
        space_env(tmp_path), hub_api=RejectingHfApi(tmp_path / "repo"), gt_download=gt_401
    )
    assert isinstance(store, st.HubStore) and not disp.configured
    queue, poller = space_app.start_scoring(store, disp, cfg, every_s=3600)
    try:
        assert queue is not None and not queue.recovered
        client = TestClient(space_app.build_app(store, disp, cfg, queue=queue))
        assert client.get("/").status_code == 200
    finally:
        poller.stop()
