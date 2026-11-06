"""Agent backends: ``claude_agent_sdk`` (Claude Code), ``litellm_loop`` (other models) and
``replay`` (scripted tool calls, no model; CI and zero-cost dry runs). Each exposes
``run(run_dir, model=None, **options) -> AgentResult``; all of them put every tool call through
the same gate (:mod:`recadbench_run.gate_hook`).

:func:`get` returns the backend wrapped so that ``efficiency.json`` is written into the run dir
when the session ends, however it ends (``recadbench agent`` and ``recadbench finish`` may run in
separate processes; finish only fills in a missing file).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

BACKENDS = ("claude_agent_sdk", "litellm_loop", "replay")


def write_efficiency(run_dir: str | Path, eff: dict) -> None:
    p = Path(run_dir) / "efficiency.json"
    try:
        p.write_text(json.dumps(eff, indent=2) + "\n", encoding="utf-8", newline="\n")
    except OSError:
        pass


class Backend:
    def __init__(self, module):
        self.module = module
        self.name = module.__name__.rsplit(".", 1)[-1]

    def run(self, run_dir, *args, **kw):
        res = None
        try:
            res = self.module.run(run_dir, *args, **kw)
            return res
        finally:
            if res is not None:
                write_efficiency(run_dir, res.efficiency)
            else:  # the backend raised: keep what is known (finish never overwrites it)
                write_efficiency(run_dir, {"turns": 0, "stop_reason":
                                           f"error: agent backend {self.name} raised"})

    def __getattr__(self, item):
        return getattr(self.module, item)


def get(name: str) -> Backend:
    if name not in BACKENDS:
        raise KeyError(f"unknown agent backend {name!r} (known: {', '.join(BACKENDS)})")
    return Backend(importlib.import_module(f"{__name__}.{name}"))
