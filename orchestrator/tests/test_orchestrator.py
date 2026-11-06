"""Orchestrator: expand (known run count), SQLite queue (claim / caps / NX limit / leases /
retries), the HTTP front + RemoteQueue, and the pull-based worker (job runner faked)."""

from __future__ import annotations

import io
import json
import tarfile
import threading
from pathlib import Path

import pytest
from recadbench_orch import worker as worker_mod
from recadbench_orch.cli import main as orch_main
from recadbench_orch.expand import ExpandError, attach_seed_plans, enqueue, expand, load, summary
from recadbench_orch.queue import JobQueue, QueueError, RemoteQueue, make_server
from recadbench_orch.worker import HostLock, Worker, pack_run, unpack_run

TINY = Path(__file__).with_name("tiny.yaml")


# ------------------------------------------------------------------------------ expand
def test_expand_tiny_known_count():
    jobs = expand(load(TINY))
    s = summary(jobs)
    assert s["runs"] == 32
    assert s["by_track"] == {"E": 12, "S": 4, "T": 16}
    assert s["by_backend"] == {"build123d": 24, "none": 4, "nx": 4}
    assert s["by_model"] == {"claude-sonnet-5-5": 24, "replay": 8}
    t = [j for j in jobs if j["payload"]["track"] == "T"]
    assert {j["payload"]["strategy"] for j in t} == {"oracle"}
    assert {j["payload"]["skillset"] for j in t} == {"build123d@v1", "none"}
    nx = [j for j in jobs if j["backend"] == "nx"]
    assert all(j["requires"] == ["nx"] and j["payload"]["strategy"] == "B" for j in nx)
    s_jobs = [j for j in jobs if j["payload"]["track"] == "S"]
    assert all(j["payload"]["skillset"] == "none" and j["requires"] == [] for j in s_jobs)
    assert len({j["run_id"] for j in jobs}) == 32
    rep = next(j for j in jobs if j["payload"]["model"] == "replay")
    assert rep["payload"]["agent_options"] == {"script": "oracle_build123d"}
    assert rep["payload"]["budgets"] == {"max_turns": 40, "max_wall_s": 1200}


@pytest.mark.parametrize("tracks,match", [
    ({"T": {"strategies": ["B"], "skillsets": ["build123d@v1"]}}, "only strategy oracle"),
    ({"E": {"strategies": ["oracle"], "skillsets": ["build123d@v1"]}}, "only in track T"),
    ({"S": {"strategies": ["B"], "skillsets": ["build123d@v1"]}}, "plan-only"),
    ({"E": {"strategies": ["B"], "skillsets": ["build123d"]}}, "<backend>@vN"),
    ({"X": {"strategies": ["B"], "skillsets": ["build123d@v1"]}}, "unknown track"),
])
def test_expand_rejects_bad_tracks(tracks, match):
    exp = {"parts": ["p"], "models": [{"id": "m"}], "tracks": tracks}
    with pytest.raises(ExpandError, match=match):
        expand(exp)


def test_expand_track_t_dev_only(tmp_path):
    (tmp_path / "splits").mkdir()
    (tmp_path / "splits" / "dev.txt").write_text("p_dev\n", encoding="utf-8")
    exp = {"parts": ["p_test"], "models": [{"id": "m"}],
           "tracks": {"T": {"strategies": ["oracle"], "skillsets": ["build123d@v1"]}}}
    with pytest.raises(ExpandError, match="dev-only"):
        expand(exp, tmp_path)
    exp["split"], exp["parts"] = "dev", None
    assert [j["payload"]["part_id"] for j in expand(exp, tmp_path)] == ["p_dev"]


def test_enqueue_needs_seed_plans_for_track_t(tmp_path):
    jobs = expand(load(TINY))
    q = JobQueue(tmp_path / "q.db")
    with pytest.raises(ExpandError, match="seed plan"):
        enqueue(jobs, q)
    notes = attach_seed_plans(jobs, lambda part: ({"part_id": part, "seed": True}, ["n1"]))
    assert notes == ["part_a: n1", "part_b: n1"]
    ids = enqueue(jobs, q)
    assert len(ids) == 32 and q.stats()["queued"] == 32
    assert enqueue(jobs, q) == ids  # idempotent on run_id


def test_cli_expand_count(capsys, monkeypatch):
    monkeypatch.delenv("RECADBENCH_DATA_DIR", raising=False)  # tiny.yaml parts are synthetic
    assert orch_main(["expand", str(TINY), "--count"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == 32


# ------------------------------------------------------------------------------- queue
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_claim_respects_caps_priority_and_nx_limit(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.db", clock=clock)
    a = q.enqueue("run", {"n": 1}, ["build123d"], "build123d", "a")
    n1 = q.enqueue("run", {"n": 2}, ["nx"], "nx", "n1")
    n2 = q.enqueue("run", {"n": 3}, ["nx"], "nx", "n2")
    s = q.enqueue("score", {"n": 4}, ["gt"], None, "s", priority=5)
    assert q.claim("phone", caps=["build123d"])["id"] == a
    assert q.claim("phone", caps=["build123d"]) is None
    j = q.claim("asus-1", host="asus", caps=["nx", "gt", "build123d"])
    assert j["id"] == s  # higher priority first
    assert q.claim("asus-1", host="asus", caps=["nx"])["id"] == n1
    assert q.claim("asus-2", host="asus", caps=["nx"]) is None  # NX parallelism 1 per host
    assert q.claim("other", host="other", caps=["nx"])["id"] == n2  # another host may
    q.complete(n1, "asus-1", {"ok": True})
    assert q.get(n1)["state"] == "done" and q.get(n1)["result"] == {"ok": True}


def test_lease_expiry_retry_and_ownership(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.db", clock=clock)
    jid = q.enqueue("run", {}, [], None, "x", max_attempts=2)
    assert q.claim("w1", lease_s=10)["id"] == jid
    with pytest.raises(QueueError):
        q.complete(jid, "w2")
    clock.t += 11  # lease lost
    j = q.claim("w2", lease_s=10)
    assert j["id"] == jid and j["attempts"] == 2
    with pytest.raises(QueueError):
        q.heartbeat(jid, "w1")
    q.heartbeat(jid, "w2", 100)
    assert q.fail(jid, "w2", "boom") == "failed"  # attempts exhausted
    jid2 = q.enqueue("run", {}, [], None, "y", max_attempts=2)
    q.claim("w", lease_s=10)
    assert q.fail(jid2, "w", "first") == "queued"
    assert q.stats() == {"queued": 1, "running": 0, "done": 0, "failed": 1}


def test_http_front_and_remote_queue(tmp_path):
    q = JobQueue(tmp_path / "q.db")
    srv = make_server(q, port=0, token="s3cret", artifacts_dir=tmp_path / "art")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        with pytest.raises(QueueError, match="401"):
            RemoteQueue(url, token="wrong").stats()
        r = RemoteQueue(url, token="s3cret")
        jid = r.enqueue("run", {"a": 1}, ["build123d"], "build123d", "rid")
        assert r.enqueue("run", {"a": 1}, ["build123d"], "build123d", "rid") == jid
        job = r.claim("phone", caps=["build123d"])
        assert job["id"] == jid and job["payload"] == {"a": 1}
        r.heartbeat(jid, "phone")
        p = r.upload(jid, b"tarball")
        assert Path(p).read_bytes() == b"tarball" and Path(p).name == "rid.tar.gz"
        r.complete(jid, "phone", {"done": 1})
        assert r.stats()["done"] == 1
        assert [j["id"] for j in r.jobs("done")] == [jid]
    finally:
        srv.shutdown()


# ------------------------------------------------------------------------------ worker
def _fake_run_job(calls):
    def run_job(payload, runs_dir, force=False, **dirs):
        calls.append(payload)
        run_dir = Path(runs_dir) / payload["run_id"]
        (run_dir / "out").mkdir(parents=True, exist_ok=True)
        (run_dir / "out" / "output.step").write_text("step", encoding="utf-8")
        (run_dir / "input").mkdir(exist_ok=True)
        (run_dir / "input" / "input.stl").write_text("big scan", encoding="utf-8")
        return {"run_id": payload["run_id"], "run_dir": str(run_dir),
                "agent": {"ok": True, "stop_reason": "end_turn", "efficiency": {"turns": 1}},
                "finish": {"ok": True}}

    return run_job


def test_worker_runs_and_enqueues_score(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("recadbench_run.run.run_job", _fake_run_job(calls))
    q = JobQueue(tmp_path / "q.db")
    q.enqueue("run", {"part_id": "p", "track": "E"}, ["build123d"], "build123d", "r1")
    q.enqueue("run", {"part_id": "p"}, ["nx"], "nx", "r2")
    w = Worker(str(tmp_path / "q.db"), "w1", ["build123d"], tmp_path / "runs")
    assert w.loop(idle_exit=True) == 1  # the nx job is not for this worker
    assert calls[0]["run_id"] == "r1"
    score = [j for j in q.jobs() if j["kind"] == "score"]
    assert len(score) == 1 and score[0]["requires"] == ["gt"]
    assert score[0]["payload"]["run_dir"].endswith("r1")
    assert q.get(1)["state"] == "done"


def test_worker_failure_is_reported(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("compose failed")

    monkeypatch.setattr("recadbench_run.run.run_job", boom)
    q = JobQueue(tmp_path / "q.db")
    q.enqueue("run", {"part_id": "p"}, [], None, "r1", max_attempts=1)
    r = Worker(str(tmp_path / "q.db"), "w1", [], tmp_path / "runs").once()
    assert r["state"] == "failed" and "compose failed" in q.get(1)["error"]


def test_pack_excludes_reproducible_inputs(tmp_path):
    run = tmp_path / "r1"
    for rel in ("input/input.stl", "input/task.yaml", ".claude/skills/x/SKILL.md",
                "lib/h.py", "out/output.step", "plan/re_plan.v1.json", "run_manifest.json"):
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_text(rel, encoding="utf-8")
    data = pack_run(run)
    names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
    assert "r1/out/output.step" in names and "r1/input/task.yaml" in names
    assert not any(n.startswith(("r1/.claude", "r1/lib")) or n == "r1/input/input.stl"
                   for n in names)
    arc = tmp_path / "a.tar.gz"
    arc.write_bytes(data)
    dest = unpack_run(arc, tmp_path / "scored")
    assert (dest / "plan" / "re_plan.v1.json").read_text(encoding="utf-8") == \
        "plan/re_plan.v1.json"


def test_unpack_refuses_path_escape(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("../evil.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    arc = tmp_path / "bad.tar.gz"
    arc.write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match="unsafe"):
        unpack_run(arc, tmp_path / "out")


def test_host_lock_is_exclusive_and_stale_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("RECADBENCH_WORKER_LOCK_DIR", str(tmp_path))
    with HostLock("nx") as lk:
        assert lk.path.is_dir()
        stale = HostLock("nx", stale_s=-1)  # treats the held lock as stale
        with stale:
            assert stale.path.is_dir()
    assert not (tmp_path / "recadbench-nx.lockdir").exists()


def test_worker_module_is_stdlib_plus_runner():
    """Termux rule: the worker and the queue import nothing outside the stdlib at module level
    (yaml only in expand; the runner is imported lazily inside the job)."""
    import ast

    stdlib = {"io", "json", "os", "socket", "tarfile", "tempfile", "threading", "time",
              "traceback", "pathlib", "__future__", "base64", "hmac", "re", "sqlite3",
              "urllib", "http"}
    for mod in (worker_mod.__file__, worker_mod.__file__.replace("worker.py", "queue.py")):
        tree = ast.parse(Path(mod).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module.split(".")[0]]
            else:
                continue
            assert set(names) <= stdlib, (mod, names)
