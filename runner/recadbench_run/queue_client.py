"""Enqueue from the runner without depending on the orchestrator package for HTTP queues.

``sqlite:///path/q.db`` (or a plain ``*.db`` path) uses ``recadbench_orch.queue`` (same host);
``http(s)://host:port`` posts to the queue server (``recadbench-orch serve``) with the bearer
token in ``RECADBENCH_QUEUE_TOKEN``. Stdlib only.
"""

from __future__ import annotations

import json
import os
import urllib.request


def _http(url: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    tok = os.environ.get("RECADBENCH_QUEUE_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def enqueue_score(queue: str, job: dict) -> dict:
    payload = {"kind": "score", "requires": ["gt"], "payload": job,
               "run_id": f"score-{job['run_id']}"}
    if queue.startswith(("http://", "https://")):
        return _http(queue, "/jobs", payload)
    from recadbench_orch.queue import JobQueue

    path = queue[len("sqlite:///"):] if queue.startswith("sqlite:///") else queue
    q = JobQueue(path)
    return {"id": q.enqueue(**payload)}
