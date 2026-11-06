"""SQLite job queue (stdlib only) + a tiny HTTP front for remote, pull-based workers.

Jobs: ``kind`` (``run`` = compose + agent + finish on a worker, ``score`` = recadbench-eval on the
evaluator host), a JSON ``payload``, the capabilities a worker must declare (``requires``, e.g.
``["build123d"]``, ``["nx"]``, ``["gt"]``) and an optional ``backend`` whose per-host parallelism
is limited at claim time (``limits``: NX = 1, 00_DECISIONS D9). Workers *pull*: ``claim`` hands
out the oldest highest-priority job a worker can run under a lease; ``heartbeat`` extends it;
``complete`` / ``fail`` end it (a failed job is re-queued until ``max_attempts``). Expired leases
go back to the queue. ``enqueue`` is idempotent on ``run_id``.

    q = JobQueue("orchestrator.db")
    q.enqueue("run", payload, requires=["build123d"], backend="build123d", run_id=...)
    job = q.claim("phone-1", host="phone-1", caps=["build123d"])

HTTP (``recadbench-orch serve``, bearer token ``RECADBENCH_QUEUE_TOKEN``): ``POST /jobs``,
``POST /claim``, ``POST /jobs/<id>/{heartbeat,complete,fail,artifact}``, ``GET /stats``,
``GET /jobs?state=``. :class:`RemoteQueue` is the client with the same methods.
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT UNIQUE,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  requires TEXT NOT NULL DEFAULT '[]',
  backend TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  worker TEXT, host TEXT, lease_until REAL,
  created REAL NOT NULL, started REAL, finished REAL,
  result TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, priority, id);
"""
STATES = ("queued", "running", "done", "failed")
DEFAULT_LIMITS = {"nx": 1}


class QueueError(RuntimeError):
    pass


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("payload", "requires", "result"):
        if d.get(k) is not None:
            d[k] = json.loads(d[k])
    return d


class JobQueue:
    def __init__(self, path: str | Path, clock=time.time):
        self.path = str(path)
        self.clock = clock
        self._lock = threading.Lock()
        with self._conn() as c:
            # WAL: readers never block the claimer; several worker processes on one file
            with contextlib.suppress(sqlite3.OperationalError):
                c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextlib.contextmanager
    def _conn(self):
        """One autocommit connection, closed on exit (sqlite3's own context manager does not
        close)."""
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    # -- producer
    def enqueue(self, kind: str, payload: dict, requires=(), backend: str | None = None,
                run_id: str | None = None, priority: int = 0, max_attempts: int = 2) -> int:
        with self._lock, self._conn() as c:
            if run_id is not None:
                r = c.execute("SELECT id FROM jobs WHERE run_id = ?", (run_id,)).fetchone()
                if r is not None:
                    return int(r["id"])
            cur = c.execute(
                "INSERT INTO jobs (run_id, kind, payload, requires, backend, priority, "
                "max_attempts, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, kind, json.dumps(payload, sort_keys=True),
                 json.dumps(sorted(set(requires))), backend, int(priority), int(max_attempts),
                 self.clock()))
            return int(cur.lastrowid)

    # -- worker side
    def claim(self, worker: str, host: str | None = None, caps=(), lease_s: float = 900.0,
              limits: dict | None = None, kinds=None) -> dict | None:
        host = host or worker
        caps = set(caps)
        limits = DEFAULT_LIMITS if limits is None else limits
        now = self.clock()
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                # expired leases: back to the queue, or failed once max_attempts is used up (a
                # job that kills its worker is not retried forever)
                c.execute("UPDATE jobs SET state = CASE WHEN attempts >= max_attempts THEN "
                          "'failed' ELSE 'queued' END, worker=NULL, lease_until=NULL, "
                          "error='lease expired', finished = CASE WHEN attempts >= max_attempts "
                          "THEN ? ELSE finished END WHERE state='running' AND lease_until < ?",
                          (now, now))
                running = {}
                for r in c.execute("SELECT backend, COUNT(*) n FROM jobs WHERE state='running' "
                                   "AND host = ? GROUP BY backend", (host,)):
                    running[r["backend"]] = r["n"]
                for r in c.execute("SELECT * FROM jobs WHERE state='queued' "
                                   "ORDER BY priority DESC, id ASC"):
                    need = set(json.loads(r["requires"]))
                    if not need <= caps or (kinds and r["kind"] not in kinds):
                        continue
                    b = r["backend"]
                    if b in limits and running.get(b, 0) >= limits[b]:
                        continue
                    cur = c.execute("UPDATE jobs SET state='running', worker=?, host=?, "
                                    "lease_until=?, started=?, attempts=attempts+1 "
                                    "WHERE id=? AND state='queued'",
                                    (worker, host, now + lease_s, now, r["id"]))
                    if cur.rowcount != 1:
                        continue
                    c.execute("COMMIT")
                    return self.get(r["id"])
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:  # keep the original error (e.g. database is locked)
                    with contextlib.suppress(sqlite3.Error):
                        c.execute("ROLLBACK")
                raise
        return None

    def _owned(self, c, job_id: int, worker: str) -> sqlite3.Row:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r is None:
            raise QueueError(f"no job {job_id}")
        if r["state"] != "running" or r["worker"] != worker:
            raise QueueError(f"job {job_id} is not running for {worker} (lease lost?)")
        return r

    def heartbeat(self, job_id: int, worker: str, lease_s: float = 900.0) -> None:
        with self._lock, self._conn() as c:
            self._owned(c, job_id, worker)
            c.execute("UPDATE jobs SET lease_until=? WHERE id=?", (self.clock() + lease_s, job_id))

    def complete(self, job_id: int, worker: str, result: dict | None = None) -> None:
        with self._lock, self._conn() as c:
            self._owned(c, job_id, worker)
            c.execute("UPDATE jobs SET state='done', finished=?, result=?, lease_until=NULL "
                      "WHERE id=?", (self.clock(), json.dumps(result or {}, default=str), job_id))

    def fail(self, job_id: int, worker: str, error: str) -> str:
        with self._lock, self._conn() as c:
            r = self._owned(c, job_id, worker)
            state = "queued" if r["attempts"] < r["max_attempts"] else "failed"
            c.execute("UPDATE jobs SET state=?, error=?, worker=NULL, lease_until=NULL, "
                      "finished=? WHERE id=?", (state, error[:4000], self.clock(), job_id))
            return state

    # -- inspection
    def get(self, job_id: int) -> dict | None:
        with self._conn() as c:
            return _row(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def jobs(self, state: str | None = None) -> list[dict]:
        with self._conn() as c:
            q = "SELECT * FROM jobs" + (" WHERE state=?" if state else "") + " ORDER BY id"
            return [_row(r) for r in c.execute(q, (state,) if state else ())]

    def stats(self) -> dict:
        with self._conn() as c:
            out = {s: 0 for s in STATES}
            for r in c.execute("SELECT state, COUNT(*) n FROM jobs GROUP BY state"):
                out[r["state"]] = r["n"]
            return out


# ------------------------------------------------------------------------------- HTTP front
def make_server(queue: JobQueue, host: str = "127.0.0.1", port: int = 0,
                token: str | None = None, artifacts_dir: str | Path | None = None
                ) -> ThreadingHTTPServer:
    token = token if token is not None else os.environ.get("RECADBENCH_QUEUE_TOKEN")
    art = Path(artifacts_dir) if artifacts_dir else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _auth(self) -> bool:
            if not token:
                return True
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got, f"Bearer {token}")

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

        def do_GET(self):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            if self.path == "/stats":
                return self._send(200, queue.stats())
            m = re.match(r"^/jobs(?:\?state=(\w+))?$", self.path)
            if m:
                return self._send(200, queue.jobs(m.group(1)))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                b = self._body()
                if self.path == "/jobs":
                    return self._send(200, {"id": queue.enqueue(
                        b["kind"], b["payload"], b.get("requires") or (), b.get("backend"),
                        b.get("run_id"), int(b.get("priority") or 0),
                        int(b.get("max_attempts") or 2))})
                if self.path == "/claim":
                    return self._send(200, {"job": queue.claim(
                        b["worker"], b.get("host"), b.get("caps") or (),
                        float(b.get("lease_s") or 900), b.get("limits"), b.get("kinds"))})
                m = re.match(r"^/jobs/(\d+)/(heartbeat|complete|fail|artifact)$", self.path)
                if not m:
                    return self._send(404, {"error": "not found"})
                jid, op = int(m.group(1)), m.group(2)
                if op == "heartbeat":
                    queue.heartbeat(jid, b["worker"], float(b.get("lease_s") or 900))
                    return self._send(200, {"ok": True})
                if op == "complete":
                    queue.complete(jid, b["worker"], b.get("result"))
                    return self._send(200, {"ok": True})
                if op == "fail":
                    return self._send(200, {"state": queue.fail(jid, b["worker"], b["error"])})
                if art is None:
                    return self._send(400, {"error": "server has no artifacts dir"})
                job = queue.get(jid) or {}
                name = re.sub(r"[^A-Za-z0-9._-]+", "-", job.get("run_id") or f"job-{jid}")
                art.mkdir(parents=True, exist_ok=True)
                p = art / f"{name}.tar.gz"
                p.write_bytes(base64.b64decode(b["data"]))
                return self._send(200, {"path": str(p)})
            except (QueueError, KeyError, ValueError) as exc:
                return self._send(409, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


class RemoteQueue:
    """HTTP client with the JobQueue methods a worker / producer needs."""

    def __init__(self, url: str, token: str | None = None, timeout: float = 60.0):
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get("RECADBENCH_QUEUE_TOKEN")
        self.timeout = timeout

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            raise QueueError(f"{method} {path}: HTTP {exc.code} "
                             f"{exc.read().decode(errors='replace')}") from exc

    def enqueue(self, kind, payload, requires=(), backend=None, run_id=None, priority=0,
                max_attempts=2) -> int:
        return int(self._req("POST", "/jobs", {
            "kind": kind, "payload": payload, "requires": list(requires), "backend": backend,
            "run_id": run_id, "priority": priority, "max_attempts": max_attempts})["id"])

    def claim(self, worker, host=None, caps=(), lease_s=900.0, limits=None, kinds=None):
        return self._req("POST", "/claim", {"worker": worker, "host": host, "caps": list(caps),
                                            "lease_s": lease_s, "limits": limits,
                                            "kinds": kinds})["job"]

    def heartbeat(self, job_id, worker, lease_s=900.0):
        self._req("POST", f"/jobs/{job_id}/heartbeat", {"worker": worker, "lease_s": lease_s})

    def complete(self, job_id, worker, result=None):
        self._req("POST", f"/jobs/{job_id}/complete", {"worker": worker, "result": result})

    def fail(self, job_id, worker, error):
        return self._req("POST", f"/jobs/{job_id}/fail", {"worker": worker, "error": error})[
            "state"]

    def upload(self, job_id, data: bytes) -> str:
        return self._req("POST", f"/jobs/{job_id}/artifact",
                         {"data": base64.b64encode(data).decode()})["path"]

    def stats(self):
        return self._req("GET", "/stats")

    def jobs(self, state=None):
        return self._req("GET", "/jobs" + (f"?state={state}" if state else ""))


def open_queue(spec: str):
    """``http(s)://...`` -> RemoteQueue, ``sqlite:///path`` or a path -> JobQueue."""
    if spec.startswith(("http://", "https://")):
        return RemoteQueue(spec)
    return JobQueue(spec[len("sqlite:///"):] if spec.startswith("sqlite:///") else spec)
