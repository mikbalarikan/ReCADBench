"""``recadbench-orch``: expand experiments, run the queue server, run workers, inspect.

    recadbench-orch expand EXP.yaml [--count] [--jobs jobs.json] [--enqueue QUEUE] [--data-dir D]
    recadbench-orch serve --db orchestrator.db [--host 0.0.0.0] [--port 8765] [--artifacts DIR]
    recadbench-orch worker --queue QUEUE --caps build123d[,nx,gt] --runs-dir DIR [--once]
    recadbench-orch status --queue QUEUE

QUEUE = ``orchestrator.db`` / ``sqlite:///orchestrator.db`` (same host) or ``http://host:port``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="recadbench-orch")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("expand")
    e.add_argument("experiment", type=Path)
    e.add_argument("--count", action="store_true", help="print the run count summary only")
    e.add_argument("--jobs", type=Path, help="write the job list (JSON)")
    e.add_argument("--enqueue", help="queue to add the jobs to (Track T seeds made here)")
    e.add_argument("--data-dir", type=Path, default=os.environ.get("RECADBENCH_DATA_DIR"))
    s = sub.add_parser("serve")
    s.add_argument("--db", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--artifacts", type=Path)
    w = sub.add_parser("worker")
    w.add_argument("--queue", required=True)
    w.add_argument("--caps", default="", help="comma list: build123d, nx, freecad, gt, ...")
    w.add_argument("--runs-dir", type=Path, default=Path("runs"))
    w.add_argument("--worker-id")
    w.add_argument("--host")
    w.add_argument("--kinds", help="comma list of job kinds (default: all)")
    w.add_argument("--once", action="store_true", help="exit when the queue has nothing")
    w.add_argument("--max-jobs", type=int)
    w.add_argument("--poll", type=float, default=15.0)
    w.add_argument("--lease", type=float, default=900.0)
    st = sub.add_parser("status")
    st.add_argument("--queue", required=True)
    a = p.parse_args(argv)

    if a.cmd == "expand":
        from .expand import attach_seed_plans, dumps, enqueue, expand, load, summary
        from .queue import open_queue

        jobs = expand(load(a.experiment), a.data_dir)
        out = {"summary": summary(jobs)}
        if a.enqueue:
            out["seed_notes"] = attach_seed_plans(jobs)
            out["enqueued"] = len(enqueue(jobs, open_queue(a.enqueue)))
        if a.jobs:
            a.jobs.write_text(dumps(jobs), encoding="utf-8", newline="\n")
        print(dumps(out if not a.count else out["summary"]), end="")
        return 0
    if a.cmd == "serve":
        from .queue import JobQueue, make_server

        srv = make_server(JobQueue(a.db), a.host, a.port, artifacts_dir=a.artifacts)
        print(json.dumps({"serving": f"http://{a.host}:{srv.server_address[1]}"}), flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if a.cmd == "worker":
        from .worker import Worker

        wk = Worker(a.queue, a.worker_id, [c for c in a.caps.split(",") if c], a.runs_dir,
                    a.host, a.lease, a.kinds.split(",") if a.kinds else None)
        wk.loop(a.poll, a.max_jobs, idle_exit=a.once)
        return 0
    if a.cmd == "status":
        from .queue import open_queue

        print(json.dumps(open_queue(a.queue).stats()))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
