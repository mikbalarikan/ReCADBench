"""LiteLLM tool loop for non-Claude models (00_DECISIONS D6).

A minimal OpenAI-style function-calling loop: system prompt = ``system_prompt.md`` (CLAUDE.md +
inlined skills, :mod:`recadbench_run.render_prompt`), tools = the dispatcher's tool surface
(file tools, Bash for a cli backend, the MCP servers of ``.mcp.json``). Every tool call goes
through the gate (inside :class:`Dispatcher.call`) before it is dispatched; denials come back as
tool errors. Budgets: turns, tokens (from the response ``usage``), cost (LiteLLM's
``completion_cost`` when it knows the model) and wall time, checked after every response.

``completion`` is injectable (tests pass a fake); by default ``litellm.completion`` is imported
lazily, so the runner does not depend on LiteLLM.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import TASK_PROMPT, AgentResult, TranscriptWriter, manifest, new_tracker
from .dispatcher import Dispatcher


def _openai_tools(schemas: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": s["name"],
                                              "description": s.get("description", ""),
                                              "parameters": s.get("input_schema") or
                                              {"type": "object"}}} for s in schemas]


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def run(run_dir: str | Path, model: str | None = None, *, completion: Callable | None = None,
        dispatcher: Dispatcher | None = None, temperature: float | None = None,
        prompt: str = TASK_PROMPT, **_: object) -> AgentResult:
    run_p = Path(run_dir).resolve()
    m = manifest(run_p)
    model = model or m["model"]["id"]
    if completion is None:
        import litellm

        completion = litellm.completion
    tracker = new_tracker(run_p)
    disp = dispatcher or Dispatcher(run_p, tracker=tracker)
    disp.tracker = tracker
    tw = TranscriptWriter(run_p)
    system = (run_p / "system_prompt.md").read_text(encoding="utf-8")
    messages: list[dict] = [{"role": "system", "content": system},
                            {"role": "user", "content": prompt}]
    tools = _openai_tools(disp.tool_schemas())
    stop = "end_turn"
    try:
        while True:
            over = tracker.exceeded()
            if over:
                stop = f"budget: {over}"
                break
            kw = {"model": model, "messages": messages, "tools": tools}
            if temperature is not None:
                kw["temperature"] = temperature
            resp = completion(**kw)
            usage = _get(resp, "usage") or {}
            cost = 0.0
            try:
                import litellm  # noqa: F401

                cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
            except Exception:
                cost = float(_get(resp, "cost", 0.0) or 0.0)
            tracker.add_turn(_get(usage, "prompt_tokens", 0), _get(usage, "completion_tokens", 0),
                             cost)
            msg = _get(_get(resp, "choices")[0], "message")
            calls = _get(msg, "tool_calls") or []
            content = _get(msg, "content") or ""
            tw.write(kind="assistant", text=content[:4000], tool_calls=len(calls))
            entry: dict = {"role": "assistant", "content": content}
            if calls:
                entry["tool_calls"] = [{"id": _get(c, "id"), "type": "function",
                                        "function": {"name": _get(_get(c, "function"), "name"),
                                                     "arguments": _get(_get(c, "function"),
                                                                       "arguments")}}
                                       for c in calls]
            messages.append(entry)
            if not calls:
                break
            for c in calls:
                fn = _get(c, "function")
                name = _get(fn, "name")
                try:
                    args = json.loads(_get(fn, "arguments") or "{}")
                except json.JSONDecodeError as exc:
                    args, text = None, f"invalid JSON arguments: {exc}"
                if args is None:
                    tracker.tool_errors += 1
                else:
                    res = disp.call(name, args)
                    text = res.text
                    tw.write(kind="tool", tool=name, ok=res.ok, denied=res.denied,
                             text=text[:2000])
                messages.append({"role": "tool", "tool_call_id": _get(c, "id"),
                                 "content": text})
    except Exception as exc:  # provider errors end the run, never the worker
        stop = f"error: {type(exc).__name__}: {exc}"[:500]
    finally:
        if dispatcher is None:
            disp.close()
        tw.close()
    eff = tracker.efficiency(stop)
    return AgentResult(ok=not stop.startswith("error"), stop_reason=stop, efficiency=eff,
                       transcript=str(tw.path), details={"model": model})
