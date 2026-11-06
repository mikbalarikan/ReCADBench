"""``recadbench`` command line (the runner owns the name; P4's ``nx-run`` is forwarded).

    recadbench compose  --strategy S --skillset <b>@vN|none [--backend b] --part P --out DIR ...
    recadbench seed-plan --part P --out seed.json        # evaluator host (RECADBENCH_GT_DIR)
    recadbench agent    --run-dir DIR [--agent-backend claude_agent_sdk|litellm_loop|replay]
    recadbench finish   --run-dir DIR [--score] [--queue URL]
    recadbench run      <compose args> [--script NAME] [--score]   # compose + agent + finish
    recadbench render-prompt --run-dir DIR
    recadbench gate     --run-dir DIR                     # PreToolUse hook (stdin JSON)
    recadbench nx-run   cad/<journal>.py [--timeout S]    # the agent's NX command (P4)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path


def _agent(a) -> int:
    from . import agents
    from .agents.base import manifest
    from .finish import finish

    m = manifest(Path(a.run_dir))
    name = a.agent_backend or m.get("agent_backend") or m.get("agent_driver", "claude_agent_sdk")
    opts = {"script": a.script} if a.script else {}
    try:
        res = agents.get(name).run(a.run_dir, model=a.model, **opts)
    except KeyError as exc:  # unknown agent backend / replay script: a clean error, no traceback
        print(json.dumps({"ok": False, "error": str(exc).strip("'\"")}))
        return 2
    out = {"ok": res.ok, "stop_reason": res.stop_reason, "efficiency": res.efficiency,
           "details": res.details}
    if a.finish:
        out["finish"] = finish(a.run_dir, efficiency=res.efficiency, score=a.score)
    print(json.dumps(out, default=str))
    return 0 if res.ok else 1


def _run(a) -> int:
    from .compose import ComposeError, request_from_args
    from .run import run_job

    req = request_from_args(a)
    job = {k: getattr(req, k) for k in ("strategy", "skillset", "part_id", "backend", "track",
                                        "harness_level", "model", "provider", "agent_backend",
                                        "replicate", "run_id", "budgets", "skillset_ref",
                                        "strategy_ref")}
    if req.seed_plan is not None:
        job["seed_plan"] = json.loads(Path(req.seed_plan).read_text(encoding="utf-8"))
    if a.script:
        job["agent_options"] = {"script": a.script}
    try:
        out = run_job(job, Path(a.out).parent, out=Path(a.out),
                      score=a.score, force=a.force, skills_dir=a.skills_dir,
                      strategies_dir=a.strategies_dir, data_dir=a.data_dir)
    except ComposeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    except KeyError as exc:
        print(json.dumps({"ok": False, "error": str(exc).strip("'\"")}))
        return 2
    print(json.dumps(out, default=str))
    return 0 if out["finish"]["ok"] and out["agent"]["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    from .compose import add_arguments

    p = argparse.ArgumentParser(prog="recadbench", description="ReCADBench runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compose", help="compose a run dir (00_DECISIONS D6)")
    add_arguments(c)
    s = sub.add_parser("seed-plan", help="Track T seed plan from the GT store (evaluator host)")
    s.add_argument("--part", required=True)
    s.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("agent", help="run an agent backend in a composed run dir")
    g.add_argument("--run-dir", type=Path, required=True)
    g.add_argument("--agent-backend", choices=["claude_agent_sdk", "litellm_loop", "replay"])
    g.add_argument("--model")
    g.add_argument("--script", help="replay script name or JSON file")
    g.add_argument("--finish", action="store_true")
    g.add_argument("--score", action="store_true")
    f = sub.add_parser("finish", help="post-run extract + export + hashes [+ score/enqueue]")
    f.add_argument("--run-dir", type=Path, required=True)
    f.add_argument("--score", action="store_true")
    f.add_argument("--queue")
    f.add_argument("--timeout", type=float, default=1800.0)
    r = sub.add_parser("run", help="compose + agent + finish")
    add_arguments(r)
    r.add_argument("--script", help="replay script name or JSON file")
    r.add_argument("--score", action="store_true")
    rp = sub.add_parser("render-prompt", help="write system_prompt.md")
    rp.add_argument("--run-dir", type=Path, required=True)
    sub.add_parser("gate", help="PreToolUse gate hook (reads hook JSON on stdin)",
                   add_help=False)
    sub.add_parser("nx-run", help="run an NX journal (backends/nx/runner)", add_help=False)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "nx-run":
        from recad_cli.recadbench import main as nx_main

        return int(nx_main(args))
    if args and args[0] == "gate":
        from .gate_hook import main as gate_main

        return int(gate_main(args[1:]))
    a = build_parser().parse_args(args)
    if a.cmd == "compose":
        from .compose import main_compose

        return main_compose(a)
    if a.cmd == "seed-plan":
        from .seed import main_seed

        return main_seed(a)
    if a.cmd == "agent":
        return _agent(a)
    if a.cmd == "finish":
        from .finish import main_finish

        return main_finish(a)
    if a.cmd == "run":
        return _run(a)
    if a.cmd == "render-prompt":
        from .render_prompt import main_render

        return main_render(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
