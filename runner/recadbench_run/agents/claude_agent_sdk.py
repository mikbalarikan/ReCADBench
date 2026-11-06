"""Claude Agent SDK backend (claude-agent-sdk, Claude Code as the harness).

The session runs in the run dir with the project settings only (``setting_sources=["project"]``:
CLAUDE.md, ``.claude/skills``, ``.claude/agents``; no user-level plugins, MCP servers or
memory), the run's ``.mcp.json`` servers only (``strict_mcp_config``), and the gate as an
in-process PreToolUse hook (:func:`recadbench_run.gate_hook.make_sdk_hook`, ``on_failure:
block``). The project's command hook in ``.claude/settings.json`` runs the same gate a second
time (defense in depth; same decision, idempotent state).

Budgets (:mod:`recadbench_run.budgets`): ``max_turns`` and ``max_cost_usd`` are passed to the SDK
and also checked on every assistant message together with ``max_tokens`` (input + output
tokens; the model is told the token budget through ``task_budget``) and ``max_wall_s``
(``asyncio`` timeout); the first budget that runs out interrupts the session.

Model ids: ``claude-opus-5-5``, ``claude-sonnet-5-5`` (any id the SDK accepts works).
Auth is whatever the local Claude Code CLI has (API key or login); this module never asks for
credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from ..compose import agent_env
from ..gate_hook import Gate, make_sdk_hook
from .base import TASK_PROMPT, AgentResult, TranscriptWriter, manifest, new_tracker

MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
HOST_ENV_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_SESSION", "CLAUDE_CODE_HOST_SESSION",
                     "CLAUDE_CODE_MESSAGING", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID",
                     "CLAUDE_CODE_ENTRYPOINT")


def _usage_tokens(usage: dict | None) -> tuple[int, int]:
    if not usage:
        return 0, 0
    tin = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
    return tin, int(usage.get("output_tokens") or 0)


def sdk_env(run: Path) -> dict[str, str]:
    env = agent_env(run)
    env = {k: v for k, v in env.items() if not k.startswith(HOST_ENV_PREFIXES)}
    # subagents and shell commands run in the foreground: a background task outlives the
    # session's last turn and its work is lost (H8 smoke run)
    env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
    return env


async def _run(run: Path, model: str, prompt: str, tracker, tw: TranscriptWriter,
               max_thinking_tokens: int | None) -> tuple[str, dict]:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        HookMatcher,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    gate = Gate(run)

    def on_decision(tool: str, d) -> None:
        if not d.allow:
            tracker.gate_denials += 1
        tw.write(kind="gate", tool=tool, **d.to_json())

    b = tracker.budgets
    mcp = json.loads((run / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers", {})
    opts = ClaudeAgentOptions(
        cwd=str(run), model=model, mcp_servers=mcp, strict_mcp_config=True,
        setting_sources=["project"], permission_mode="bypassPermissions",
        disallowed_tools=["WebFetch", "WebSearch"],
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[make_sdk_hook(gate, on_decision)],
                                          on_failure="block")]},
        max_turns=int(b.max_turns) if b.max_turns else None,
        max_budget_usd=float(b.max_cost_usd) if b.max_cost_usd else None,
        task_budget={"total": int(b.max_tokens)} if b.max_tokens else None,
        env=sdk_env(run), max_thinking_tokens=max_thinking_tokens,
        stderr=lambda line: tw.write(kind="stderr", text=line[:2000]),
    )
    import claude_agent_sdk as sdk

    task_started = getattr(sdk, "TaskStartedMessage", ())
    task_done = tuple(t for t in (getattr(sdk, "TaskNotificationMessage", None),
                                  getattr(sdk, "TaskUpdatedMessage", None)) if t)
    terminal = getattr(sdk, "TERMINAL_TASK_STATUSES", frozenset(
        {"completed", "failed", "stopped", "killed"}))
    stop, result = "end_turn", {}
    seen_ids: set[str] = set()
    active: set[str] = set()
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(prompt)
        # receive_messages, not receive_response: a session that started a subagent task keeps
        # running until the task reports back (the H8 smoke run ended while its modeler
        # subagent still ran); stop at a result only when no task is active
        async for msg in client.receive_messages():
            if task_started and isinstance(msg, task_started):
                active.add(msg.task_id)
                tw.write(kind="task_started", task_id=msg.task_id, text=msg.description[:300])
                continue
            if task_done and isinstance(msg, task_done):
                status = getattr(msg, "status", None) or (getattr(msg, "patch", None) or {}
                                                          ).get("status")
                if status in terminal:
                    active.discard(msg.task_id)
                    tw.write(kind="task_done", task_id=msg.task_id, status=status)
                continue
            if isinstance(msg, AssistantMessage):
                # one API message arrives as several AssistantMessages (one per content
                # block) carrying the same usage: count it once
                mid = msg.message_id or f"anon-{len(seen_ids)}"
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    tin, tout = _usage_tokens(msg.usage)
                    tracker.add_turn(tin, tout)
                for blk in msg.content:
                    if isinstance(blk, ToolUseBlock):
                        tw.write(kind="tool_use", tool=blk.name, input=blk.input)
                    elif isinstance(blk, TextBlock):
                        tw.write(kind="text", text=blk.text[:4000])
                over = tracker.exceeded()
                if over:
                    stop = f"budget: {over}"
                    await client.interrupt()
                    break
            elif isinstance(msg, ResultMessage):
                result = {"subtype": msg.subtype, "num_turns": msg.num_turns,
                          "total_cost_usd": msg.total_cost_usd, "usage": msg.usage,
                          "is_error": msg.is_error, "stop_reason": msg.stop_reason,
                          "errors": msg.errors, "result": (msg.result or "")[:2000]}
                tw.write(kind="result", active_tasks=sorted(active), **result)
                if msg.subtype != "success":
                    stop = msg.subtype
                    break
                if not active:
                    break
    return stop, result


def run(run_dir: str | Path, model: str | None = None, *, prompt: str = TASK_PROMPT,
        max_thinking_tokens: int | None = None, **_: object) -> AgentResult:
    run = Path(run_dir).resolve()
    m = manifest(run)
    model = model or m["model"]["id"]
    tracker = new_tracker(run)
    tw = TranscriptWriter(run)
    wall = tracker.budgets.max_wall_s
    result: dict = {}
    try:
        stop, result = asyncio.run(asyncio.wait_for(
            _run(run, model, prompt, tracker, tw, max_thinking_tokens),
            timeout=float(wall) if wall else None))
    except TimeoutError:
        stop = f"budget: max_wall_s ({wall})"
    except Exception as exc:  # CLI / auth / transport errors end the run, never the worker
        stop = f"error: {type(exc).__name__}: {exc}"[:500]
    finally:
        tw.close()
    live = (tracker.tokens_in, tracker.tokens_out)
    if result.get("usage"):  # the CLI's session totals are authoritative (live counts are
        # per-message estimates for the budget checks)
        tracker.tokens_in, tracker.tokens_out = _usage_tokens(result["usage"])
    if result.get("total_cost_usd") is not None:
        tracker.cost_usd = float(result["total_cost_usd"])
    if result.get("num_turns"):
        tracker.turns = int(result["num_turns"])
    eff = tracker.efficiency(stop)
    if result.get("usage"):
        eff["cache_read_tokens"] = int(result["usage"].get("cache_read_input_tokens") or 0)
    ok = not stop.startswith("error") and not result.get("is_error", False)
    return AgentResult(ok=ok, stop_reason=stop, efficiency=eff, transcript=str(tw.path),
                       details={"result": result, "model": model, "live_tokens": live,
                                "auth_env": bool(os.environ.get("ANTHROPIC_API_KEY"))})
