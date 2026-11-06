"""Regression tests for the H8/P10 review findings on the orchestrator (each failed on 5b26f8b):
heartbeat stops after one transient error, lease loss crashes Worker.once(), expired leases
ignore max_attempts, claim errors end the worker loop, a retry deletes the live run dir, expand
accepts typos / replicates 0 / one skillset ref for every backend."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest
from recadbench_orch.expand import ExpandError, expand
from recadbench_orch.queue import JobQueue
from recadbench_orch.worker import Worker


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_heartbeat_survives_a_transient_error(tmp_path):
    db = tmp_path / "q.db"
    JobQueue(db).enqueue("run", {"part_id": "p"}, ["build123d"], "build123d", "r1")
    w1 = Worker(str(db), "w1", ["build123d"], tmp_path, host="h", lease_s=0.9)
    w2 = Worker(str(db), "w2", ["build123d"], tmp_path, host="h", lease_s=60)
    real, calls, seen = w1.q.heartbeat, {"n": 0}, {}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient network error")
        return real(*a, **k)

    w1.q.heartbeat = flaky

    def slow(job):
        time.sleep(1.6)  # > lease: only the heartbeat keeps the job
        seen["second"] = w2.q.claim("w2", "h", ["build123d"], 60)
        return {"ok": True}

    w1.run_job = slow
    r = w1.once()
    assert r["state"] == "done"
    assert seen["second"] is None and calls["n"] >= 2


def test_lease_loss_does_not_crash_the_worker(tmp_path):
    db = tmp_path / "q.db"
    q = JobQueue(db)
    q.enqueue("run", {"part_id": "p"}, [], None, "r1")
    w = Worker(str(db), "w1", [], tmp_path, host="h", lease_s=60)

    def stolen(job):
        with sqlite3.connect(db) as c:  # another worker took the job over meanwhile
            c.execute("UPDATE jobs SET worker='w2' WHERE id=?", (job["id"],))
        return {"ok": True}

    w.run_job = stolen
    r = w.once()
    assert r["state"] == "lease_lost"

    def stolen_then_fail(job):
        stolen(job)
        raise RuntimeError("run failed")

    q.enqueue("run", {"part_id": "p"}, [], None, "r2")
    w.run_job = stolen_then_fail
    r = w.once()
    assert r["state"] == "lease_lost" and "run failed" in r["error"]


def test_expired_leases_respect_max_attempts(tmp_path):
    clock = Clock()
    q = JobQueue(tmp_path / "q.db", clock=clock)
    jid = q.enqueue("run", {}, [], None, "crashy", max_attempts=2)
    got = []
    for k in range(5):
        j = q.claim(f"w{k}", lease_s=10)
        got.append(j and j["attempts"])
        clock.t += 11  # the worker died without fail()
    q.claim("probe", lease_s=10)  # runs the expiry pass once more
    assert got == [1, 2, None, None, None]
    j = q.get(jid)
    assert j["state"] == "failed" and j["error"] == "lease expired"


def test_claim_errors_do_not_end_the_worker_loop(tmp_path):
    w = Worker(str(tmp_path / "q.db"), "w1", [], tmp_path)
    w.claim_tries, w.claim_backoff_s = 2, 0.01
    n = {"calls": 0}

    def locked(*a, **k):
        n["calls"] += 1
        raise sqlite3.OperationalError("database is locked")

    w.q.claim = locked
    r = w.once()
    assert r["state"] == "claim_error" and "locked" in r["error"] and n["calls"] == 2


def test_claim_keeps_the_original_error(tmp_path, monkeypatch):
    q = JobQueue(tmp_path / "q.db")
    q.enqueue("run", {}, [], None, "r1")
    real = q._conn

    class Boom:
        def __init__(self, c):
            self.c = c
            self.n = 0

        def execute(self, sql, *a):
            if sql.startswith("UPDATE jobs SET state='running'"):
                # SQLite already ended the transaction (as after a busy timeout / I/O error)
                self.c.execute("ROLLBACK")
                raise sqlite3.OperationalError("disk I/O error")
            return self.c.execute(sql, *a)

        @property
        def in_transaction(self):
            return self.c.in_transaction

    import contextlib

    @contextlib.contextmanager
    def conn():
        with real() as c:
            yield Boom(c)

    monkeypatch.setattr(q, "_conn", conn)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        q.claim("w1")


def test_multi_process_claims_are_unique(tmp_path):
    """Several processes claiming from one SQLite file: no job twice, no lock error."""
    db = tmp_path / "q.db"
    q = JobQueue(db)
    for i in range(60):
        q.enqueue("run", {"i": i}, [], None, f"r{i}")
    code = textwrap.dedent(f"""
        import json, sys
        from recadbench_orch.queue import JobQueue
        q = JobQueue({str(db)!r})
        got = []
        while True:
            j = q.claim(sys.argv[1], host=sys.argv[1], lease_s=3600)
            if j is None:
                break
            got.append(j["id"])
        print(json.dumps(got))
    """)
    ps = [subprocess.Popen([sys.executable, "-c", code, f"w{k}"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True) for k in range(3)]
    outs = [p.communicate(timeout=300) for p in ps]
    assert all(p.returncode == 0 for p in ps), [o[1][-500:] for o in outs]
    ids = [i for o in outs for i in json.loads(o[0].strip().splitlines()[-1])]
    assert len(ids) == 60 and len(set(ids)) == 60


def test_each_attempt_gets_its_own_run_dir(tmp_path, monkeypatch):
    seen = []

    def fake(payload, runs, *, force=False, out=None, **dirs):
        seen.append(out)
        out.mkdir(parents=True, exist_ok=True)
        return {"run_id": payload["run_id"], "run_dir": str(out),
                "agent": {"ok": True, "stop_reason": "x", "efficiency": {}},
                "finish": {"ok": True}}

    monkeypatch.setattr("recadbench_run.run.run_job", fake)
    w = Worker(str(tmp_path / "q.db"), "w1", [], tmp_path / "runs")
    w.run_job({"id": 1, "attempts": 1, "run_id": "r1", "payload": {"part_id": "p"}})
    w.run_job({"id": 1, "attempts": 2, "run_id": "r1", "payload": {"part_id": "p"}})
    assert [p.name for p in seen] == ["r1", "r1.a2"]


# ------------------------------------------------------------------------------ expand
BASE = {"name": "x", "parts": ["p1"], "models": [{"id": "m1"}],
        "tracks": {"E": {"strategies": ["B"], "skillsets": ["build123d@v1", "nx@v1"]}}}


@pytest.mark.parametrize("bad,match", [
    ({"exclude": [{"tracks": "S"}]}, "unknown key"),
    ({"replicates": 0}, "replicates"),
    ({"replicates": "2"}, "replicates"),
    ({"refs": {"skillset": "build123d-v1"}}, "refs.skillset"),
    ({"models": [{"id": "claude-sonnet-5-5", "agent_backend": "replay"}]}, "replay"),
])
def test_expand_rejects_bad_experiments(bad, match):
    with pytest.raises(ExpandError, match=match):
        expand({**BASE, **bad})


def test_expand_refs_and_defaults_per_skillset():
    jobs = expand({**BASE, "refs": {"skillset": {"nx@v1": "abc123"}}})
    refs = {j["payload"]["skillset"]: j["payload"]["skillset_ref"] for j in jobs}
    assert refs == {"build123d@v1": None, "nx@v1": "abc123"}
    assert {j["payload"]["harness_level"] for j in jobs} == {"H1"}
    rep = expand({**BASE, "models": [{"id": "replay", "agent_backend": "replay"}]})
    assert {j["payload"]["provider"] for j in rep} == {"replay"}
