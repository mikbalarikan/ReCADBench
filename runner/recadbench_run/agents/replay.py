"""Replay agent backend: scripted tool calls, no model (CI, zero-cost dry runs).

Each step is dispatched exactly like a model's tool call (gate first, then the local tool or the
MCP server of ``.mcp.json``), counted as one turn, and logged to the transcript. Steps marked
``expect`` assert the gate decision; any mismatch makes the run fail (``ok = False``).
"""

from __future__ import annotations

from pathlib import Path

from .base import AgentResult, TranscriptWriter, new_tracker
from .dispatcher import Dispatcher
from .replay_scripts import load


def run(run_dir: str | Path, script: str = "oracle_build123d", *, dispatcher=None,
        **_: object) -> AgentResult:
    run = Path(run_dir).resolve()
    tracker = new_tracker(run)
    disp = dispatcher or Dispatcher(run, tracker=tracker)
    disp.tracker = tracker
    tw = TranscriptWriter(run)
    mismatches: list[str] = []
    stop = "script_end"
    gen = load(script)({"run_dir": str(run)})
    try:
        res = None
        while True:
            try:
                step = gen.send(res) if res is not None else next(gen)
            except StopIteration:
                break
            over = tracker.exceeded()
            if over:
                stop = f"budget: {over}"
                break
            tracker.add_turn()
            res = disp.call(step.tool, step.args)
            tw.write(kind="tool", tool=step.tool, args=_short(step.args), ok=res.ok,
                     denied=res.denied, text=res.text[:2000], note=step.note)
            if step.expect == "deny" and not res.denied:
                mismatches.append(f"{step.tool}: expected deny, was allowed")
            if step.expect == "allow" and (res.denied or not res.ok):
                mismatches.append(f"{step.tool}: expected allow + ok, got {res.text[:300]}")
    finally:
        if dispatcher is None:
            disp.close()
        tw.close()
    eff = tracker.efficiency(stop)
    return AgentResult(ok=not mismatches, stop_reason=stop, efficiency=eff,
                       transcript=str(tw.path), details={"mismatches": mismatches})


def _short(args: dict) -> dict:
    return {k: (v[:200] + "...") if isinstance(v, str) and len(v) > 200 else v
            for k, v in args.items()}
