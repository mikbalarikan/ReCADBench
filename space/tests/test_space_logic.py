"""Pure-logic tests for the Space (no network, no GT, no evaluator run)."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SPACE = Path(__file__).resolve().parents[1]
if str(SPACE) not in sys.path:
    sys.path.insert(0, str(SPACE))

import jobs  # noqa: E402
import store as st  # noqa: E402
import submit  # noqa: E402

import leaderboard as lb  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
TASKS = ["r385_hs01_motor_mount", "od_h11_thermoblock"]
META = {
    "submitter_name": "Ada",
    "submission_name": "agent-x",
    "agent_url": "https://example.org",
    "model": "m",
    "cad_backend": "build123d",
    "harness": "h",
    "human_in_loop": False,
    "notes": "",
    "agree_to_publish": True,
}


class FakeDispatcher(jobs.Dispatcher):
    def __init__(self, fail: bool = False):
        self.calls: list[tuple] = []
        self.states: dict[str, str] = {}
        self.summaries: dict[str, dict] = {}
        self.fail = fail

    def dispatch(self, submission_id, sha, zip_ref):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((submission_id, sha, zip_ref))
        job = f"job-{len(self.calls)}"
        self.states[job] = jobs.QUEUED
        return job

    def status(self, job_id):
        return self.states.get(job_id, jobs.UNKNOWN)

    def collect(self, submission_id, job_id, store):
        return self.summaries.get(job_id)


def fake_inspect(sha: str, tasks=("r385_hs01_motor_mount",), missing=()):
    def _inspect(path, task_ids):
        return SimpleNamespace(
            meta=dict(META),
            tasks={t: {"output.step": f"{t}/output.step"} for t in tasks},
            sha256=sha,
            missing=list(missing),
            warnings=[],
        )

    return _inspect


def sha_of(i: int) -> str:
    return f"{i:08x}" * 8  # distinct 8-char prefix -> distinct submission ids


@pytest.fixture
def env(tmp_path):
    store = st.LocalStore(tmp_path / "store")
    disp = FakeDispatcher()
    zp = tmp_path / "s.zip"
    zp.write_bytes(b"PK-not-really")
    return store, disp, zp


def submit_one(store, disp, zp, sha, profile=None, now=NOW, **kw):
    profile = {"preferred_username": "alice"} if profile is None else profile
    return submit.handle_submission(
        zp, profile, store, disp, now, task_ids=TASKS, inspect=fake_inspect(sha), **kw
    )


# --- submit ----------------------------------------------------------------------------------


def test_oauth_missing_is_an_error(env):
    store, disp, zp = env
    for profile in (None, {}, SimpleNamespace(username=None)):
        msg = submit.handle_submission(
            zp, profile, store, disp, NOW, task_ids=TASKS, inspect=fake_inspect(sha_of(1))
        )
        assert msg.startswith("Error") and "sign in" in msg
    assert store.list_results() == [] and disp.calls == []


def test_profile_object_username(env):
    store, disp, zp = env
    msg = submit_one(store, disp, zp, sha_of(1), profile=SimpleNamespace(username="bob"))
    assert "queued" in msg
    assert store.list_results()[0]["hf_user"] == "bob"


def test_happy_path_writes_pending_row_and_dispatches(env):
    store, disp, zp = env
    msg = submit_one(store, disp, zp, sha_of(7))
    sid = "sub-" + sha_of(7)[:8]
    assert sid in msg
    (row,) = store.list_results()
    assert row["status"] == "pending" and row["job_id"] == "job-1"
    assert row["hf_user"] == "alice" and row["validated"] is False
    assert row["submitted_at"] == "2026-11-18T12:00:00Z"
    assert row["submitter_name"] == "Ada" and row["human_in_loop"] is False
    assert "notes" not in row and "agree_to_publish" not in row
    assert disp.calls == [(sid, sha_of(7), store.zip_ref(sha_of(7)))]
    assert Path(store.zip_ref(sha_of(7))).is_file()


def test_size_limit(env):
    store, disp, zp = env
    msg = submit_one(store, disp, zp, sha_of(1), zip_limit=3)
    assert msg.startswith("Error") and "limit" in msg
    assert store.list_results() == []


def test_inspect_error_is_reported(env):
    store, disp, zp = env

    def bad(path, task_ids):
        raise ValueError("unexpected top-level entry 'x'")

    msg = submit.handle_submission(zp, {"username": "a"}, store, disp, NOW, inspect=bad)
    assert msg == "Error: unexpected top-level entry 'x'"
    assert store.list_results() == []


def test_no_task_is_rejected(env):
    store, disp, zp = env
    msg = submit.handle_submission(
        zp, {"username": "a"}, store, disp, NOW, task_ids=TASKS,
        inspect=fake_inspect(sha_of(1), tasks=()),
    )  # fmt: skip
    assert msg.startswith("Error") and store.list_results() == []


def test_dedupe_by_sha(env):
    store, disp, zp = env
    submit_one(store, disp, zp, sha_of(3))
    msg = submit_one(store, disp, zp, sha_of(3), profile={"username": "someone_else"})
    assert "already submitted" in msg
    assert len(store.list_results()) == 1 and len(disp.calls) == 1


def test_failed_submission_can_be_retried_in_place(env):
    store, disp, zp = env
    submit_one(store, disp, zp, sha_of(3))
    sid = store.list_results()[0]["submission_id"]
    store.update_result(sid, status="failed", status_message="scoring job failed")
    msg = submit_one(store, disp, zp, sha_of(3), now=NOW + timedelta(hours=1))
    assert "queued" in msg
    (row,) = store.list_results()
    assert row["status"] == "pending" and row["status_message"] == "" and row["job_id"] == "job-2"


def test_failed_retry_by_another_user_keeps_attribution(env, monkeypatch):
    store, disp, zp = env
    submit_one(store, disp, zp, sha_of(3))
    sid = store.list_results()[0]["submission_id"]
    store.update_result(sid, status="failed")
    msg = submit_one(store, disp, zp, sha_of(3), profile={"username": "mallory"})
    assert msg.startswith("Error") and "another user" in msg
    (row,) = store.list_results()
    assert row["hf_user"] == "alice" and row["status"] == "failed" and len(disp.calls) == 1
    # an admin may retry; the row stays alice's
    monkeypatch.setenv("RCB_ADMINS", "ops")
    assert "queued" in submit_one(store, disp, zp, sha_of(3), profile={"username": "ops"})
    (row,) = store.list_results()
    assert row["hf_user"] == "alice" and row["status"] == "pending"


def test_submission_id_prefix_collision_gets_longer_id(env):
    store, disp, zp = env
    sha_a = "abcdef01" + "0" * 56
    sha_b = "abcdef01" + "1" * 56
    store.append_result({"submission_id": "sub-abcdef01", "sha256": sha_a, "status": "done"})
    msg = submit_one(store, disp, zp, sha_b)
    assert "sub-abcdef011111" in msg, msg
    rows = store.list_results()
    assert [r["submission_id"] for r in rows] == ["sub-abcdef01", "sub-abcdef011111"]
    assert disp.calls[0][0] == "sub-abcdef011111"
    # a failed retry of the long-id zip reuses its own id
    store.update_result("sub-abcdef011111", status="failed")
    assert "sub-abcdef011111" in submit_one(store, disp, zp, sha_b, now=NOW + timedelta(hours=1))
    assert len(store.list_results()) == 2


def test_rate_limit_three_per_user_per_utc_day(env):
    store, disp, zp = env
    for i in range(3):
        assert "queued" in submit_one(store, disp, zp, sha_of(10 + i))
    msg = submit_one(store, disp, zp, sha_of(20))
    assert msg.startswith("Error") and "per user per UTC day" in msg
    # other users are not affected
    assert "queued" in submit_one(store, disp, zp, sha_of(21), profile={"username": "bob"})
    # next UTC day resets the count
    tomorrow = datetime(2026, 11, 19, 0, 0, 1, tzinfo=UTC)
    assert "queued" in submit_one(store, disp, zp, sha_of(22), now=tomorrow)


def test_rate_limit_uses_utc_day_not_local_time(env):
    store, disp, zp = env
    late = datetime(2026, 11, 18, 23, 30, tzinfo=UTC)
    for i in range(3):
        submit_one(store, disp, zp, sha_of(30 + i), now=late)
    # 01:00 in UTC+3 on the 19th is still the 18th in UTC
    from datetime import timezone

    same_utc_day = datetime(2026, 11, 19, 2, 0, tzinfo=timezone(timedelta(hours=3)))
    assert "limit" in submit_one(store, disp, zp, sha_of(40), now=same_utc_day)


def test_dispatch_failure_marks_row_failed(tmp_path):
    store = st.LocalStore(tmp_path)
    zp = tmp_path / "z.zip"
    zp.write_bytes(b"x")
    msg = submit_one(store, FakeDispatcher(fail=True), zp, sha_of(5))
    assert msg.startswith("Error") and "boom" not in msg
    (row,) = store.list_results()
    assert row["status"] == "failed"


# --- sweep / refresh -------------------------------------------------------------------------


def test_sweep_pending_marks_stale_rows_failed(env):
    store, disp, zp = env
    submit_one(store, disp, zp, sha_of(1), now=NOW - timedelta(hours=5))
    submit_one(store, disp, zp, sha_of(2), now=NOW - timedelta(minutes=30))
    submit_one(store, disp, zp, sha_of(3), now=NOW - timedelta(hours=3))
    old_done = "sub-" + sha_of(3)[:8]
    store.update_result(old_done, status="done")
    swept = submit.sweep_pending(store, disp, max_age_h=2, now=NOW)
    assert swept == ["sub-" + sha_of(1)[:8]]
    by_id = {r["submission_id"]: r for r in store.list_results()}
    assert by_id["sub-" + sha_of(1)[:8]]["status"] == "failed"
    assert by_id["sub-" + sha_of(2)[:8]]["status"] == "pending"
    assert by_id[old_done]["status"] == "done"


def test_sweep_merges_finished_jobs_first(env):
    store, disp, zp = env
    submit_one(store, disp, zp, sha_of(1), now=NOW - timedelta(hours=5))
    disp.states["job-1"] = jobs.COMPLETED
    disp.summaries["job-1"] = {"open_total": 50.0, "n_solved": 1, "per_task": {}}
    assert submit.sweep_pending(store, disp, now=NOW) == []
    (row,) = store.list_results()
    assert row["status"] == "done" and row["open_total"] == 50.0


def test_refresh_jobs_states(env):
    store, disp, zp = env
    for i in range(3):
        submit_one(store, disp, zp, sha_of(100 + i))
    disp.states.update({"job-1": jobs.RUNNING, "job-2": jobs.ERROR, "job-3": jobs.COMPLETED})
    disp.summaries["job-3"] = {
        "open_total": 61.5,
        "n_solved": 2,
        "n_gate_ok": 2,
        "eval_version": "0.1.0+open.abc",
        "submitter_name": "SPOOFED",  # identity comes from the zip, never from the job
        "status": "done",
        "per_task": {"r385_hs01_motor_mount": {"status": "ok", "total": 70.0}},
    }
    assert jobs.refresh_jobs(store, disp) == 3
    rows = store.list_results()
    assert [r["status"] for r in rows] == ["scoring", "failed", "done"]
    assert rows[2]["open_total"] == 61.5 and rows[2]["submitter_name"] == "Ada"


# --- store allowlist -------------------------------------------------------------------------


def test_row_allowlist_drops_private_keys(tmp_path):
    store = st.LocalStore(tmp_path)
    row = {
        "submission_id": "sub-deadbeef",
        "sha256": sha_of(9),
        "status": "done",
        "open_total": 40.0,
        "faces_gt": 38,
        "n_ref": 12,
        "gt_plan": {"d": 12.5},
        "result_path": "C:/private/result.json",
        "per_task": {
            "r385_hs01_motor_mount": {
                "status": "ok",
                "total": 40.0,
                "S1": 20.0,
                "face_ratio_bucket": "0.35-1.5",
                "faces_gt": 38,
                "band_tight_eff_mm": 0.15,
                "registration": {"T": [1, 0, 0]},
                "key_dims": [{"name": "bore", "ok": True, "nominal": 12.345, "measured": 12.3}],
            },
            "../evil": {"status": "ok"},
        },
        "declared": {"n_counted": 5, "unmapped": 0, "blob": [1, 2, 3]},
    }
    store.append_result(row)
    text = (tmp_path / "results.jsonl").read_text(encoding="utf-8")
    banned = ("faces_gt", "n_ref", "gt_plan", "result_path", "12.345", "band_tight",
              "registration", "nominal", "measured", "evil", "blob")  # fmt: skip
    for bad in banned:
        assert bad not in text, bad
    (stored,) = store.list_results()
    task = stored["per_task"]["r385_hs01_motor_mount"]
    assert task["key_dims"] == [{"name": "bore", "ok": True}]
    assert stored["declared"] == {"n_counted": 5, "unmapped": 0}
    # update_result goes through the same allowlist
    store.update_result("sub-deadbeef", faces_gt=99, gt_volume=1.0, validated=True)
    text = (tmp_path / "results.jsonl").read_text(encoding="utf-8")
    assert "faces_gt" not in text and "gt_volume" not in text
    assert store.get_result("sub-deadbeef")["validated"] is True


def test_store_ids_and_paths_are_validated(tmp_path):
    store = st.LocalStore(tmp_path)
    with pytest.raises(ValueError):
        store.append_result({"submission_id": "../x"})
    with pytest.raises(ValueError):
        store.put_zip("../../etc", tmp_path / "x")
    with pytest.raises(KeyError):
        store.update_result("sub-00000000", status="done")
    store.append_result({"submission_id": "sub-00000001"})
    with pytest.raises(ValueError):
        store.append_result({"submission_id": "sub-00000001"})
    assert store.get_report_html("../../secret") is None
    assert store.get_report_file("sub-00000001", "../results.jsonl") is None


def test_reports_roundtrip_and_delete(tmp_path):
    store = st.LocalStore(tmp_path / "s")
    pub = tmp_path / "public"
    (pub / "renders").mkdir(parents=True)
    (pub / "report.html").write_text("<html>ok</html>", encoding="utf-8")
    (pub / "renders" / "a.png").write_bytes(b"\x89PNG")
    store.append_result({"submission_id": "sub-0000abcd", "status": "done"})
    store.put_report("sub-0000abcd", pub)
    assert store.get_report_html("sub-0000abcd") == "<html>ok</html>"
    assert store.get_report_file("sub-0000abcd", "renders/a.png").is_file()
    assert store.delete_result("sub-0000abcd") is True
    assert store.get_report_html("sub-0000abcd") is None
    assert store.delete_result("sub-0000abcd") is False


def test_concurrent_updates_do_not_lose_rows(tmp_path):
    import threading

    store = st.LocalStore(tmp_path)
    ids = [f"sub-{i:08x}" for i in range(20)]

    def add(sid):
        st.LocalStore(tmp_path).append_result({"submission_id": sid, "status": "pending"})

    threads = [threading.Thread(target=add, args=(s,)) for s in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["submission_id"] for r in store.list_results()) == ids


# --- hub store with a fake api ---------------------------------------------------------------


class FakeHfApi:
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
        data = (
            path_or_fileobj
            if isinstance(path_or_fileobj, bytes)
            else Path(path_or_fileobj).read_bytes()
        )
        dst.write_bytes(data)
        self.commits += 1

    def upload_folder(self, folder_path, path_in_repo, repo_id, repo_type=None, token=None,
                      commit_message=None):  # fmt: skip
        import shutil

        shutil.copytree(folder_path, self.root / path_in_repo, dirs_exist_ok=True)

    def delete_folder(self, path_in_repo, repo_id, repo_type=None, token=None, commit_message=None):
        import shutil

        shutil.rmtree(self.root / path_in_repo, ignore_errors=True)


def test_hubstore_with_fake_api(tmp_path):
    api = FakeHfApi(tmp_path / "repo")
    store = st.HubStore("org/subs", "tok", cache_dir=tmp_path / "cache", api=api)
    disp = FakeDispatcher()
    zp = tmp_path / "s.zip"
    zp.write_bytes(b"zip")
    assert "queued" in submit_one(store, disp, zp, sha_of(77))
    assert (tmp_path / "repo" / "submissions" / f"{sha_of(77)}.zip").is_file()
    sid = "sub-" + sha_of(77)[:8]
    (tmp_path / "repo" / "jobs" / sid).mkdir(parents=True)
    (tmp_path / "repo" / "jobs" / sid / "summary.json").write_text(
        json.dumps({"open_total": 12.0, "faces_gt": 3}), encoding="utf-8"
    )
    hub_disp = jobs.HFJobsDispatcher("org/subs", "org/gt", "img", "tok", api=SimpleNamespace())
    summary = hub_disp.collect(sid, "j", store)
    store.update_result(sid, status="done", **jobs.row_fields_from_summary(summary))
    row = store.get_result(sid)
    assert row["open_total"] == 12.0 and "faces_gt" not in row
    assert "faces_gt" not in (tmp_path / "repo" / "results.jsonl").read_text(encoding="utf-8")


def test_hubstore_caches_results_between_writes(tmp_path):
    class CountingApi(FakeHfApi):
        downloads = 0

        def hf_hub_download(self, *a, **kw):
            CountingApi.downloads += 1
            return super().hf_hub_download(*a, **kw)

    now = [100.0]
    api = CountingApi(tmp_path / "repo")
    store = st.HubStore("org/subs", "tok", cache_dir=tmp_path / "c", api=api,
                        cache_ttl_s=10, clock=lambda: now[0])  # fmt: skip
    store.append_result({"submission_id": "sub-00000001", "status": "pending"})
    n = CountingApi.downloads
    for _ in range(5):
        assert [r["submission_id"] for r in store.list_results()] == ["sub-00000001"]
    assert CountingApi.downloads == n  # served from the cache filled by the write
    store.list_results()[0]["status"] = "mutated"  # callers get copies
    assert store.get_result("sub-00000001")["status"] == "pending"
    store.update_result("sub-00000001", status="done")
    assert store.list_results()[0]["status"] == "done"  # own write is visible at once
    # an outside change shows up after the TTL
    (tmp_path / "repo" / "results.jsonl").write_text(
        json.dumps({"submission_id": "sub-00000002", "status": "done"}) + "\n", encoding="utf-8"
    )
    assert store.list_results()[0]["submission_id"] == "sub-00000001"
    now[0] += 11
    assert [r["submission_id"] for r in store.list_results()] == ["sub-00000002"]


# --- dispatchers -----------------------------------------------------------------------------


def test_job_script_uploads_public_only_and_validates_ids():
    script = jobs.build_job_script("org/subs", "org/gt", "sub-0123abcd", sha_of(1))
    compile(script, "<job>", "exec")
    assert "private" not in script
    assert '"reports/" + SID' in script and "summary.json" in script
    assert "RECADBENCH_GT_DIR" in script
    with pytest.raises(ValueError):
        jobs.build_job_script("org/subs", "org/gt", "sub-x'; import os", sha_of(1))
    with pytest.raises(ValueError):
        jobs.build_job_script("org/subs", "org/gt", "sub-0123abcd", sha_of(1), task_ids=["a'b"])


def _run_job_script(script, tmp_path, monkeypatch, fake_rc=0):
    """Execute the job script with fake huggingface_hub / subprocess; return what it did."""
    import types

    seen: dict = {"uploads": []}

    class Api:
        def __init__(self, token=None):
            seen["api_token"] = token

        def upload_folder(self, **kw):
            seen["uploads"].append(kw["path_in_repo"])

        def upload_file(self, **kw):
            seen["uploads"].append(kw["path_in_repo"])

    hub = types.ModuleType("huggingface_hub")
    hub.HfApi = Api
    hub.hf_hub_download = lambda *a, **k: str(tmp_path / "s.zip")
    hub.snapshot_download = lambda *a, **k: str(tmp_path / "gt")
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    def fake_run(cmd, env):
        seen["cmd"], seen["env"] = cmd, env
        seen["os_env_has_token"] = "HF_TOKEN" in __import__("os").environ
        if "--tasks-file" in cmd:
            seen["tasks"] = Path(cmd[cmd.index("--tasks-file") + 1]).read_text(encoding="utf-8")
        out = Path(cmd[cmd.index("--out-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=fake_rc)

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_secret2")
    script = script.replace("/tmp/rcb-out", (tmp_path / "out").as_posix())
    script = script.replace("/tmp/rcb-tasks.txt", (tmp_path / "tasks.txt").as_posix())
    exec(compile(script, "<job>", "exec"), {"__name__": "__main__"})
    return seen


def test_job_script_drops_token_and_passes_tasks_file(tmp_path, monkeypatch):
    script = jobs.build_job_script(
        "org/subs", "org/gt", "sub-0123abcd", sha_of(1), timeout_s=75, task_ids=TASKS
    )
    seen = _run_job_script(script, tmp_path, monkeypatch)
    assert seen["api_token"] == "hf_secret"  # downloads / uploads still authenticated
    assert "HF_TOKEN" not in seen["env"] and "HUGGING_FACE_HUB_TOKEN" not in seen["env"]
    assert "hf_secret" not in json.dumps(seen["env"]) and not seen["os_env_has_token"]
    assert seen["env"]["RECADBENCH_GT_DIR"].endswith("parts")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--timeout") + 1] == "75"
    assert seen["tasks"].split() == TASKS
    assert seen["uploads"] == ["jobs/sub-0123abcd/summary.json"]  # no public/ dir, no private/
    # without a task list the evaluator default is used
    seen = _run_job_script(
        jobs.build_job_script("org/subs", "org/gt", "sub-0123abcd", sha_of(1)),
        tmp_path, monkeypatch,
    )  # fmt: skip
    assert "--tasks-file" not in seen["cmd"]


def test_per_task_timeout_fits_job_budget():
    assert jobs.parse_duration_s("30m") == 1800 and jobs.parse_duration_s("1h") == 3600
    assert jobs.parse_duration_s("90s") == 90 and jobs.parse_duration_s(600) == 600
    for timeout in ("30m", "45m", "1h"):
        for n in (1, 5, 10, 20):
            t = jobs.per_task_timeout_s(timeout, n)
            budget = jobs.parse_duration_s(timeout)
            assert n * jobs.CHILDREN_PER_TASK * t + jobs.JOB_RESERVE_S <= budget
            assert t >= jobs.MIN_TASK_TIMEOUT_S
    with pytest.raises(ValueError):
        jobs.per_task_timeout_s("5m", 10)


def test_hf_dispatcher_with_fake_api():
    class Api:
        def run_job(self, **kw):
            self.kw = kw
            return SimpleNamespace(id="J1")

        def inspect_job(self, job_id, namespace=None):
            return SimpleNamespace(status=SimpleNamespace(stage="COMPLETED"))

    api = Api()
    d = jobs.HFJobsDispatcher("org/subs", "org/gt", "img:1", "tok", api=api)
    assert d.dispatch("sub-0123abcd", sha_of(2), "submissions/x.zip") == "J1"
    assert api.kw["flavor"] == "cpu-upgrade" and api.kw["timeout"] == "30m"
    assert api.kw["secrets"] == {"HF_TOKEN": "tok"} and api.kw["image"] == "img:1"
    assert d.status("J1") == jobs.COMPLETED
    # the per-task timeout the job passes fits 10 tasks x 2 children in the 30 min job
    script = api.kw["command"][2]
    t = int(script.split('"--timeout", ')[1].split("]")[0].strip("'\""))
    assert 10 * 2 * t + jobs.JOB_RESERVE_S <= 30 * 60
    assert "TASK_IDS = []" in script
    # the Space's task set is embedded and the budget follows its size
    d = jobs.HFJobsDispatcher("org/subs", "org/gt", "img:1", "tok", api=api, task_ids=TASKS)
    d.dispatch("sub-0123abcd", sha_of(2), "submissions/x.zip")
    script = api.kw["command"][2]
    assert repr(TASKS) in script and d.task_timeout_s == (1800 - jobs.JOB_RESERVE_S) // 4


def test_local_dispatcher_command_and_status(tmp_path):
    launched = {}

    class Proc:
        rc = None

        def poll(self):
            return self.rc

    def popen(cmd, env, stdout, stderr):
        launched["cmd"], launched["env"] = cmd, env
        launched["proc"] = Proc()
        return launched["proc"]

    d = jobs.LocalDispatcher(tmp_path, gt_dir="/gt", popen=popen, python="py")
    job = d.dispatch("sub-0123abcd", sha_of(3), "/z.zip")
    cmd = launched["cmd"]
    assert cmd[:4] == ["py", "-m", "recadbench_eval.cli", "score-submission"]
    assert "--layer" in cmd and cmd[cmd.index("--layer") + 1] == "open"
    assert cmd[cmd.index("--submission-id") + 1] == "sub-0123abcd"
    assert launched["env"]["RECADBENCH_GT_DIR"] == "/gt"
    assert d.status(job) == jobs.RUNNING
    launched["proc"].rc = 0
    assert d.status(job) == jobs.ERROR  # exit 0 but no summary
    out = tmp_path / "sub-0123abcd"
    (out / "summary.json").write_text(json.dumps({"open_total": 1.0}), encoding="utf-8")
    (out / "public").mkdir()
    (out / "public" / "report.html").write_text("r", encoding="utf-8")
    assert d.status(job) == jobs.COMPLETED
    store = st.LocalStore(tmp_path / "store")
    assert d.collect("sub-0123abcd", job, store) == {"open_total": 1.0}
    assert store.get_report_html("sub-0123abcd") == "r"
    # after a restart the handle is gone; the summary still proves completion
    assert jobs.LocalDispatcher(tmp_path, gt_dir="/gt").status(job) == jobs.COMPLETED
    with pytest.raises(RuntimeError):
        jobs.LocalDispatcher(tmp_path, gt_dir="", popen=popen).dispatch("sub-0123abcd", "", "")


# --- admin -----------------------------------------------------------------------------------


def test_admin_actions(env, monkeypatch):
    store, disp, zp = env
    submit_one(store, disp, zp, sha_of(8))
    sid = "sub-" + sha_of(8)[:8]
    monkeypatch.setenv("RCB_ADMINS", "root, ops")
    assert submit.admin_action("promote", sid, {"username": "alice"}, store, disp).startswith(
        "Error"
    )
    assert submit.admin_action("promote", sid, None, store, disp).startswith("Error")
    admin = {"username": "ops"}
    assert submit.is_admin(admin) and not submit.is_admin({"username": "alice"})
    submit.admin_action("promote", sid, admin, store, disp)
    assert store.get_result(sid)["validated"] is True
    submit.admin_action("demote", sid, admin, store, disp)
    assert store.get_result(sid)["validated"] is False
    store.update_result(sid, status="failed")
    assert "rescoring" in submit.admin_action("rescore", sid, admin, store, disp, now=NOW)
    assert store.get_result(sid)["status"] == "pending" and len(disp.calls) == 2
    assert submit.admin_action("explode", sid, admin, store, disp).startswith("Error")
    submit.admin_action("delete", sid, admin, store, disp)
    assert store.get_result(sid) is None


# --- leaderboard -----------------------------------------------------------------------------


def _done(sid, total, **kw):
    return {"submission_id": sid, "status": "done", "open_total": total, "n_solved": 1,
            "n_gate_ok": 1, "submitted_at": "2026-11-18T10:00:00Z", **kw}  # fmt: skip


def test_leaderboard_table_order_and_badges():
    rows = [
        _done("sub-00000001", 30.0, submission_name="a"),
        {"submission_id": "sub-00000002", "status": "pending", "submission_name": "b"},
        _done("sub-00000003", 70.0, submission_name="c", validated=True, human_in_loop=True),
        _done("sub-00000004", 50.0, submission_name="ref", reference=True),
    ]
    headers, data = lb.table(rows)
    assert headers[6] == "Open /95"
    assert [d[1] for d in data] == ["c", "ref", "a", "b"]
    assert [d[0] for d in data] == ["1", "2", "3", ""]
    assert data[0][9] == "validated" and data[0][10] == "yes"
    assert data[1][9] == "reference, unvalidated" and data[1][12] == ""
    assert data[2][9] == "unvalidated" and "sub-00000001" in data[2][12]
    assert data[3][9] == "queued" and data[3][6] == "-"


def test_leaderboard_user_strings_are_plain_text():
    # str cells render as text in gr.Dataframe: no escaping (it would show literally)
    rows = [_done("sub-00000001", 1.0, submission_name="gpt_4o & claude (v2)\nx", model="my_model")]
    _, data = lb.table(rows)
    assert data[0][1] == "gpt_4o & claude (v2) x" and data[0][3] == "my_model"
    _, data = lb.table([_done("sub-00000001", 1.0, submission_name="n" * 200)])
    assert len(data[0][1]) == 80
    # only the last (Report) column is markdown, and it holds no user string
    _, data = lb.table([_done("sub-00000001", 1.0, submission_name="[l](javascript:1)")])
    assert data[0][-1] == "[report](reports/sub-00000001.html)"
    # markdown / HTML places still escape
    assert "<script>" not in lb._md_text("<script>") and "](" not in lb._md_text("[l](j:1)")
    rows = [_done("sub-00000001", 1.0, submission_name="<script>x</script>")]
    assert "<script>" not in lb.heatmap_html(rows, TASKS)


def test_heatmap():
    rows = [
        _done("sub-00000001", 40.0, per_task={
            "r385_hs01_motor_mount": {"status": "ok", "total": 80.0},
            "od_h11_thermoblock": {"status": "gate_fail", "total": 0.0},
        }),
        {"submission_id": "sub-00000002", "status": "pending"},
    ]  # fmt: skip
    out = lb.heatmap_html(rows, TASKS)
    assert "80.0" in out and "hs01" in out and "h11" in out
    assert "sub-00000002" not in out
    assert lb.heatmap_html([], TASKS).startswith("<p>")


def test_tasks_never_show_extra_fields(tmp_path):
    p = tmp_path / "tasks.json"
    p.write_text(json.dumps([{
        "id": "r385_hs01_motor_mount", "tolerance_class": "B", "tier": 2, "triangles": 299403,
        "known_defects": ["holes <b>"], "bbox_mm": [12.345, 6, 7], "key_dims": {"d": 9.87},
    }]), encoding="utf-8")  # fmt: skip
    tasks = lb.load_tasks(p, TASKS)
    assert tasks[0] == {
        "id": "r385_hs01_motor_mount", "tolerance_class": "B", "tier": 2, "triangles": 299403,
        "known_defects": ["holes <b>"],
    }  # fmt: skip
    assert tasks[1] == {"id": "od_h11_thermoblock"}
    card = lb.task_card_html(tasks[0], "org/inputs")
    assert "12.345" not in card and "9.87" not in card and "holes &lt;b&gt;" in card
    assert "datasets/org/inputs/resolve/main/parts/r385_hs01_motor_mount/input.stl" in card


def test_reference_rows(tmp_path):
    p = tmp_path / "ref.json"
    p.write_text(json.dumps([{"submission_id": "sub-0000000a", "open_total": 5, "faces_gt": 1}]),
                 encoding="utf-8")  # fmt: skip
    (r,) = lb.load_reference_rows(p)
    assert r["reference"] is True and r["status"] == "done" and "faces_gt" not in r


def test_load_task_ids(tmp_path):
    assert len(submit.load_task_ids(tmp_path / "nope.txt")) == 10
    f = tmp_path / "ids.txt"
    f.write_text("# dev\na_one\n\nb_two  # c\n", encoding="utf-8")
    assert submit.load_task_ids(f) == ["a_one", "b_two"]


# --- integration with the real stdlib zip checker (when present) ------------------------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_real_inspect_zip(tmp_path):
    pytest.importorskip("recadbench_eval.submission")
    store = st.LocalStore(tmp_path / "store")
    disp = FakeDispatcher()
    good = tmp_path / "good.zip"
    good.write_bytes(_zip_bytes({
        "meta.json": json.dumps(META).encode(),
        "r385_hs01_motor_mount/output.step": b"ISO-10303-21;\nEND-ISO-10303-21;\n",
    }))  # fmt: skip
    msg = submit.handle_submission(good, {"username": "a"}, store, disp, NOW, task_ids=TASKS)
    assert "queued" in msg, msg
    assert "1 task(s) without output.step" in msg
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip_bytes({"meta.json": json.dumps(META).encode(), "../evil": b"x"}))
    msg = submit.handle_submission(bad, {"username": "a"}, store, disp, NOW, task_ids=TASKS)
    assert msg.startswith("Error")
    assert len(store.list_results()) == 1


# --- app wiring (no server) ------------------------------------------------------------------


def test_app_builds_and_serves_reports(tmp_path, monkeypatch):
    pytest.importorskip("gradio")
    import app as space_app
    from fastapi.testclient import TestClient

    env = {"RCB_MODE": "local", "RCB_LOCAL_ROOT": str(tmp_path), "RECADBENCH_GT_DIR": "/gt"}
    store, disp, cfg = space_app.build_backends(env)
    store.append_result(_done("sub-0000beef", 10.0))
    pub = tmp_path / "pub"
    (pub / "renders").mkdir(parents=True)
    (pub / "report.html").write_text("<p>report</p>", encoding="utf-8")
    (pub / "renders" / "x.png").write_bytes(b"\x89PNG")
    store.put_report("sub-0000beef", pub)
    client = TestClient(space_app.build_app(store, disp, cfg))
    r = client.get("/reports/sub-0000beef/report.html")
    assert r.status_code == 200 and "report" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert client.get("/reports/sub-0000beef/renders/x.png").status_code == 200
    r = client.get("/reports/sub-0000beef.html", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert client.get("/reports/sub-0000beef/results.jsonl").status_code == 404
    assert client.get("/reports/sub-nothere0/report.html").status_code == 404
    assert client.get("/").status_code == 200
