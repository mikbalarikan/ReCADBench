"""Agent backends: ``claude_agent_sdk`` (Claude Code), ``litellm_loop`` (other models) and
``replay`` (scripted tool calls, no model; CI and zero-cost dry runs). Each exposes
``run(run_dir, model=None, **options) -> AgentResult``; all of them put every tool call through
the same gate (:mod:`recadbench_run.gate_hook`)."""

from __future__ import annotations

import importlib

BACKENDS = ("claude_agent_sdk", "litellm_loop", "replay")


def get(name: str):
    if name not in BACKENDS:
        raise KeyError(f"unknown agent backend {name!r} (known: {', '.join(BACKENDS)})")
    return importlib.import_module(f"{__name__}.{name}")
