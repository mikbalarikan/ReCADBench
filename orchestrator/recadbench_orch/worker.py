"""Pull-based worker (Termux-capable: stdlib + recadbench-run; no Windows-only calls).

    recadbench-orch worker --queue http://evalhost:8765 --caps build123d --runs-dir ~/runs
    recadbench-orch worker --queue orchestrator.db --caps build123d,nx,gt --runs-dir runs --once

Loop: claim a job the declared capabilities allow -> run it under a lease (a heartbeat thread
extends it) -> complete / fail. ``run`` jobs: compose + agent + finish (:func:`recadbench_run.run.
run_job`), then a ``score`` job (requires ``gt``) is enqueued; a remote worker first uploads the
run dir (without the reproducible ``input/``, ``.claude/``, ``lib/``, ``_shared/``) to the queue
server. ``score`` jobs (evaluator host, ``gt`` capability): ``recadbench-eval score`` ->
result.json.

NX: the queue never hands a host a second running ``nx`` job (limit 1, 00_DECISIONS D9) and the
worker additionally holds a host-local lock directory while an NX job runs (``nx_run`` keeps its
own run_journal lock too). A worker only claims ``nx`` jobs when it declares the ``nx`` capability.
"""

from __future__ import annotations

import io
import json
import os
import socket
import tarfile
import tempfile
import threading
import time
import traceback
from pathlib import Path

from .queue import QueueError, open_queue

EXCLUDE_UPLOAD = ("input/input.stl", ".claude/", "lib/", "_shared/")


class HostLock:
    """Host-wide lock: an atomically created directory (portable, no fcntl/msvcrt). A lock older
    than ``stale_s`` without refresh is taken over."""

    def __init__(self, name: str, stale_s: float = 6 * 3600):
        self.path = Path(os.environ.get("RECADBENCH_WORKER_LOCK_DIR", tempfile.gettempdir())) \
            / f"recadbench-{name}.lockdir"
        self.stale_s = stale_s

    def __enter__(self):
        while True:
            try:
                self.path.mkdir()
                (self.path / "owner").write_text(f"{os.getpid()} {time.time()}\n")
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue
                if age > self.stale_s:
                    self._release()
                    continue
                time.sleep(2)

    def _release(self):
        try:
            for p in self.path.iterdir():
                p.unlink()
            self.path.rmdir()
        except OSError:
            pass

    def __exit__(self, *exc):
        self._release()


def pack_run(run_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(run_dir.rglob("*")):
            rel = p.relative_to(run_dir).as_posix()
            if p.is_file() and not any(rel == e or rel.startswith(e) for e in EXCLUDE_UPLOAD):
                tf.add(p, arcname=f"{run_dir.name}/{rel}")
    return buf.getvalue()


def unpack_run(archive: Path, dest_root: Path) -> Path:
    dest_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        root = None
        for m in tf.getmembers():
            parts = Path(m.name).parts
            if m.name.startswith(("/", "\\")) or ".." in parts or not (m.isfile() or m.isdir()):
                raise ValueError(f"unsafe archive member {m.name!r}")
            root = root or parts[0]
        tf.extractall(dest_root, filter="data")
    return dest_root / root


class Worker:
    def __init__(self, queue: str, worker_id: str | None = None, caps=(), runs_dir=".",
                 host: str | None = None, lease_s: float = 900.0, kinds=None,
                 dirs: dict | None = None):
        self.queue_spec = queue
        self.q = open_queue(queue)
        self.remote = queue.startswith(("http://", "https://"))
        self.worker = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.host = host or socket.gethostname()
        self.caps = sorted(set(caps))
        self.runs = Path(runs_dir).resolve()
        self.lease_s = lease_s
        self.kinds = kinds
        self.dirs = dirs or {}

    def _beat(self, job_id: int, stop: threading.Event, lost: threading.Event) -> None:
        """Extend the lease every lease/3 s. A transient error (network, a locked database) is
        retried with a short backoff; only an explicit lease loss (QueueError) stops it."""
        wait = self.lease_s / 3
        while not stop.wait(wait):
            try:
                self.q.heartbeat(job_id, self.worker, self.lease_s)
                wait = self.lease_s / 3
            except QueueError:
                lost.set()
                return
            except Exception:  # transient: retry soon, well inside the lease
                wait = min(self.lease_s / 3, max(0.05, self.lease_s / 20))

    claim_tries = 5
    claim_backoff_s = 0.5

    def _claim(self):
        """claim with retry + backoff on transient queue errors (database locked, network)."""
        delay, tries = self.claim_backoff_s, self.claim_tries
        for k in range(tries):
            try:
                return self.q.claim(self.worker, self.host, self.caps, self.lease_s,
                                    kinds=self.kinds)
            except Exception:
                if k == tries - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 10.0)
        return None

    def once(self) -> dict | None:
        try:
            job = self._claim()
        except Exception as exc:  # the loop goes on; the next poll tries again
            return {"id": None, "state": "claim_error", "error": f"{type(exc).__name__}: {exc}"}
        if job is None:
            return None
        stop, lost = threading.Event(), threading.Event()
        threading.Thread(target=self._beat, args=(job["id"], stop, lost), daemon=True).start()
        try:
            if job["kind"] == "run":
                if job.get("backend") == "nx":
                    with HostLock("nx"):
                        res = self.run_job(job)
                else:
                    res = self.run_job(job)
            elif job["kind"] == "score":
                res = self.score_job(job)
            else:
                raise ValueError(f"unknown job kind {job['kind']!r}")
            stop.set()
        except Exception as exc:
            stop.set()
            err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-3000:]}"
            try:
                state = self.q.fail(job["id"], self.worker, err)
            except QueueError as qe:  # lease lost meanwhile: another worker owns the job now
                return {"id": job["id"], "state": "lease_lost", "error": f"{exc}; {qe}"}
            return {"id": job["id"], "state": state, "error": str(exc)}
        try:
            self.q.complete(job["id"], self.worker, res)
        except QueueError as qe:
            return {"id": job["id"], "state": "lease_lost", "error": str(qe), "result": res,
                    "heartbeat_lost": lost.is_set()}
        return {"id": job["id"], "state": "done", "result": res}

    def run_job(self, job: dict) -> dict:
        from recadbench_run.run import run_job

        payload = dict(job["payload"])
        payload.setdefault("run_id", job.get("run_id"))
        # one run dir per attempt: a retry (or a second worker after a lost lease) never
        # deletes the dir another attempt may still be writing
        rid = payload.get("run_id") or f"job-{job['id']}"
        attempt = int(job.get("attempts") or 1)
        out_dir = self.runs / (rid if attempt <= 1 else f"{rid}.a{attempt}")
        out = run_job(payload, self.runs, force=True, out=out_dir, **self.dirs)
        run_dir = Path(out["run_dir"])
        score_payload = {"run_id": out["run_id"], "part_id": payload["part_id"],
                         "run_dir": str(run_dir)}
        if self.remote:
            score_payload["artifact"] = self.q.upload(job["id"], pack_run(run_dir))
        sid = self.q.enqueue("score", score_payload, requires=["gt"],
                             run_id=f"score-{out['run_id']}")
        return {"run_id": out["run_id"], "run_dir": str(run_dir),
                "agent": {k: out["agent"][k] for k in ("ok", "stop_reason", "efficiency")},
                "finish_ok": out["finish"]["ok"], "score_job": sid}

    def score_job(self, job: dict) -> dict:
        from recadbench_run.finish import run_score

        p = job["payload"]
        run_dir = Path(p["run_dir"])
        if p.get("artifact"):
            run_dir = unpack_run(Path(p["artifact"]), self.runs / "scored")
        res = run_score(run_dir, p["part_id"])
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or res.get("skipped") or "score failed")
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        return {"result": str(run_dir / "result.json"), "total": result["scores"].get("total"),
                "pass": result.get("pass")}

    def loop(self, poll_s: float = 15.0, max_jobs: int | None = None,
             idle_exit: bool = False) -> int:
        n = 0
        while max_jobs is None or n < max_jobs:
            r = self.once()
            if r is None:
                if idle_exit:
                    break
                time.sleep(poll_s)
                continue
            if r.get("state") == "claim_error":
                print(json.dumps({"worker": self.worker, **r}), flush=True)
                time.sleep(poll_s)
                continue
            n += 1
            print(json.dumps({"worker": self.worker, **{k: r[k] for k in ("id", "state")}}),
                  flush=True)
        return n
