"""One job end to end on a worker: compose -> agent session -> finish [-> score / enqueue]."""

from __future__ import annotations

import json
from pathlib import Path

from . import agents
from .compose import ComposeRequest, compose, resolve_model
from .finish import finish

JOB_KEYS = ("strategy", "skillset", "part_id", "backend", "track", "harness_level", "model",
            "provider", "agent_backend", "replicate", "run_id", "seed_plan", "budgets",
            "skillset_ref", "strategy_ref")


def request_from_job(job: dict, out: Path, **dirs) -> ComposeRequest:
    kw = {k: job[k] for k in JOB_KEYS if k in job and job[k] is not None}
    kw["part_id"] = job["part_id"]
    return ComposeRequest(out=out, **kw, **{k: v for k, v in dirs.items() if v is not None})


def run_job(job: dict, runs_dir: str | Path, *, score: bool = False, enqueue=None,
            agent_options: dict | None = None, force: bool = False, out: Path | None = None,
            **dirs) -> dict:
    """``job``: the payload of a queue job (keys of :class:`ComposeRequest`, ``part_id``,
    optional ``seed_plan`` dict for Track T, ``agent_options``). ``out``: exact run dir
    (default ``<runs_dir>/<run_id>``)."""
    run_id = job.get("run_id")
    req = request_from_job(job, Path(out) if out else Path(runs_dir) / (run_id or "pending"),
                           **dirs)
    resolve_model(req)
    if run_id is None and out is None:
        from .compose import default_run_id, resolve_track

        backend = req.backend or (req.skillset.split("@")[0] if req.skillset != "none"
                                  else "none")
        req.run_id = default_run_id(req, resolve_track(req.strategy, backend, req.track),
                                    backend)
        req.out = Path(runs_dir) / req.run_id
    req.force = force
    run_dir = compose(req)
    backend = agents.get(req.agent_backend)
    opts = dict(job.get("agent_options") or {})
    opts.update(agent_options or {})
    res = backend.run(run_dir, model=req.model, **opts)
    fin = finish(run_dir, efficiency=res.efficiency, score=score, enqueue=enqueue)
    out = {"run_id": req.run_id or run_dir.name, "run_dir": str(run_dir),
           "agent": {"ok": res.ok, "stop_reason": res.stop_reason,
                     "efficiency": res.efficiency, "details": res.details},
           "finish": fin}
    (run_dir / ".recadbench" / "run_job.json").write_text(
        json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    return out
