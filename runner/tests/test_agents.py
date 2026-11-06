"""Agent backends without a model or a CAD server: dispatcher, LiteLLM loop with a scripted fake
completion, replay expectations, budgets, Claude Agent SDK wiring (no CLI call)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from recadbench_run.agents import get, litellm_loop, replay
from recadbench_run.agents.dispatcher import Dispatcher
from recadbench_run.budgets import Budgets, BudgetTracker
from recadbench_run.gate_hook import Gate
from runner_helpers import mini_run, write_plan


class FakeServer:
    """Stands in for an MCP server: records calls, answers ok."""

    def __init__(self, tools):
        self._tools = [{"name": t, "description": t, "inputSchema": {"type": "object"}}
                       for t in tools]
        self.calls = []

    def tools(self):
        return self._tools

    def call(self, tool, args):
        self.calls.append((tool, args))
        return True, f"ok {tool}"

    def close(self):
        pass


def run_dir(tmp_path, budgets=None):
    run = mini_run(tmp_path)
    m = {"run_id": "t", "track": "E", "part_id": "syn_part", "replicate": 0,
         "strategy": {"id": "B", "git_sha": "x"}, "model": {"id": "fake", "provider": "x"},
         "spec_version": "0.3.0", "budgets": budgets or {"max_turns": 20},
         "harness_level": "H3", "backend": {"id": "build123d"},
         "skillset": {"id": "build123d@v1"}}
    (run / "run_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (run / "system_prompt.md").write_text("system\n", encoding="utf-8")
    (run / "CLAUDE.md").write_text("task\n", encoding="utf-8")
    return run


def disp(run):
    return Dispatcher(run, env={}, servers={"build123d": FakeServer(["execute", "measure",
                                                                    "import_cad_file"])},
                      start_servers=False)


def test_dispatcher_local_tools_and_gate(tmp_path):
    run = run_dir(tmp_path)
    d = disp(run)
    assert d.call("Write", {"file_path": "cad/a.txt", "content": "x\ny\n"}).ok
    r = d.call("Read", {"file_path": "cad/a.txt"})
    assert r.ok and r.text.splitlines()[1].endswith("\ty")
    assert d.call("Edit", {"file_path": "cad/a.txt", "old_string": "y", "new_string": "z"}).ok
    assert "cad/a.txt:2:z" in d.call("Grep", {"pattern": "z"}).text
    assert "cad/a.txt" in d.call("Glob", {"pattern": "cad/*.txt"}).text
    r = d.call("mcp__build123d__measure", {})
    assert r.denied and "CAD is locked" in r.text
    write_plan(run, 1)
    assert d.call("mcp__build123d__measure", {}).ok
    assert d.call("mcp__build123d__import_cad_file", {"path": "input/input.stl"}).denied
    assert d.servers["build123d"].calls == [("measure", {})]
    assert d.call("Bash", {"command": "ls"}).denied
    names = [t["name"] for t in d.tool_schemas()]
    assert "mcp__build123d__measure" in names and "Bash" not in names


def fake_completion(script):
    """A scripted model: each call returns the next message (tool calls or final text)."""
    calls = iter(script)

    def completion(**kw):
        msg = next(calls)
        tc = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
              for i, (n, a) in enumerate(msg.get("tools", []))]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=msg.get("text", ""), tool_calls=tc))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10))

    return completion


def test_litellm_loop_gates_every_call(tmp_path):
    run = run_dir(tmp_path)
    write_plan(run, 1)
    d = disp(run)
    comp = fake_completion([
        {"tools": [("Read", {"file_path": "plan/re_plan.v1.json"}),
                   ("mcp__build123d__import_cad_file", {"path": "input/input.stl"})]},
        {"tools": [("mcp__build123d__execute", {"code": "Box(1, 1, 1)"}),
                   ("WebFetch", {"url": "http://x"})]},
        {"text": "DONE"},
    ])
    res = litellm_loop.run(run, completion=comp, dispatcher=d)
    assert res.ok and res.stop_reason == "end_turn"
    assert res.efficiency["turns"] == 3 and res.efficiency["gate_denials"] == 2
    assert res.efficiency["tokens_in"] == 300 and res.efficiency["tokens_out"] == 30
    assert d.servers["build123d"].calls == [("execute", {"code": "Box(1, 1, 1)"})]
    log = [json.loads(x) for x in (run / ".recadbench" / "gate_log.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [x["tool"] for x in log] == ["Read", "mcp__build123d__import_cad_file",
                                        "mcp__build123d__execute", "WebFetch"]


def test_litellm_loop_stops_at_turn_budget(tmp_path):
    run = run_dir(tmp_path, {"max_turns": 2})
    loop = [{"tools": [("Glob", {"pattern": "*"})]}] * 10
    res = litellm_loop.run(run, completion=fake_completion(loop), dispatcher=disp(run))
    assert res.stop_reason == "budget: max_turns (2)" and res.efficiency["turns"] == 2


def test_replay_expectations(tmp_path):
    run = run_dir(tmp_path)
    script = tmp_path / "s.json"
    script.write_text(json.dumps([
        {"tool": "Bash", "args": {"command": "ls"}, "expect": "deny"},
        {"tool": "mcp__build123d__measure", "args": {}, "expect": "allow"},
    ]), encoding="utf-8")
    res = replay.run(run, str(script), dispatcher=disp(run))
    assert not res.ok and "expected allow" in res.details["mismatches"][0]
    write_plan(run, 1)
    res = replay.run(run, str(script), dispatcher=disp(run))
    assert res.ok and res.efficiency["gate_denials"] == 1


def test_budgets_merge_and_tracker():
    b = Budgets.merge({"max_wall_s": 3600, "max_turns": 200}, {"max_turns": 40,
                                                                "max_cost_usd": 2.5})
    assert b.to_manifest() == {"max_wall_s": 3600, "max_turns": 40, "max_cost_usd": 2.5}
    with pytest.raises(ValueError):
        Budgets.merge({"max_turns": 0}, None)
    with pytest.raises(ValueError):
        Budgets.merge({"turns": 3}, None)
    now = [0.0]
    t = BudgetTracker(Budgets(max_wall_s=10, max_tokens=1000), clock=lambda: now[0]).start()
    t.add_turn(400, 100)
    assert t.exceeded() is None
    t.add_turn(400, 100)
    assert t.exceeded() == "max_tokens (1000)"
    t2 = BudgetTracker(Budgets(max_wall_s=10), clock=lambda: now[0]).start()
    now[0] = 11.0
    assert t2.exceeded() == "max_wall_s (10)"
    eff = t.efficiency("x")
    assert eff["tokens_in"] == 800 and eff["stop_reason"] == "x" and eff["turns"] == 2


def test_agent_registry():
    assert get("replay") is replay
    with pytest.raises(KeyError):
        get("gpt")


def test_claude_agent_sdk_wiring(tmp_path, monkeypatch):
    """The SDK backend passes the gate as a PreToolUse hook, the run's MCP servers only, the
    project settings only and the budgets; the CLI is not started (client is faked)."""
    sdk = pytest.importorskip("claude_agent_sdk")
    from recadbench_run.agents import claude_agent_sdk as backend

    run = run_dir(tmp_path, {"max_turns": 40, "max_wall_s": 1200, "max_tokens": 50000})
    (run / ".mcp.json").write_text(json.dumps({"mcpServers": {"build123d": {
        "command": "build123d-mcp"}}}), encoding="utf-8")
    seen = {}

    class FakeClient:
        def __init__(self, options):
            seen["options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def query(self, prompt):
            seen["prompt"] = prompt
            hook = seen["options"].hooks["PreToolUse"][0].hooks[0]
            seen["deny"] = await hook({"tool_name": "Bash", "tool_input": {"command": "ls"}},
                                      "t1", None)

        async def receive_messages(self):
            yield sdk.AssistantMessage(content=[sdk.TextBlock(text="DONE")], model="m",
                                       usage={"input_tokens": 1000, "output_tokens": 50})
            yield sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                    is_error=False, num_turns=1, session_id="s",
                                    total_cost_usd=0.01,
                                    usage={"input_tokens": 1000, "output_tokens": 50,
                                           "cache_read_input_tokens": 7})

        async def interrupt(self):
            seen["interrupted"] = True

    monkeypatch.setattr(sdk, "ClaudeSDKClient", FakeClient)
    monkeypatch.setenv("RECADBENCH_GT_DIR", "/secret/gt")
    res = backend.run(run, model="claude-sonnet-5-5")
    o = seen["options"]
    assert res.ok and res.stop_reason == "end_turn"
    assert o.model == "claude-sonnet-5-5" and o.max_turns == 40 and o.cwd == str(run)
    assert o.setting_sources == ["project"] and o.strict_mcp_config
    assert set(o.mcp_servers) == {"build123d"}
    assert o.task_budget == {"total": 50000}
    assert "RECADBENCH_GT_DIR" not in o.env and not any(k.startswith("CLAUDECODE")
                                                         for k in o.env)
    assert seen["deny"]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert res.efficiency["gate_denials"] == 1 and res.efficiency["cost_usd"] == 0.01
    assert res.efficiency["tokens_in"] == 1000 and res.efficiency["cache_read_tokens"] == 7


def test_gate_used_by_dispatcher_is_the_run_gate(tmp_path):
    run = run_dir(tmp_path)
    assert isinstance(disp(run).gate, Gate)


def test_claude_agent_sdk_waits_for_subagent_tasks(tmp_path, monkeypatch):
    """A result while a subagent task is active does not end the session (the H8 smoke run
    lost its modeler subagent that way); duplicate per-block messages are counted once."""
    sdk = pytest.importorskip("claude_agent_sdk")
    if not hasattr(sdk, "TaskStartedMessage"):
        pytest.skip("SDK without task messages")
    from recadbench_run.agents import claude_agent_sdk as backend

    run = run_dir(tmp_path, {"max_turns": 40})
    (run / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    def result(n, cost):
        return sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                 is_error=False, num_turns=n, session_id="s",
                                 total_cost_usd=cost,
                                 usage={"input_tokens": 10, "cache_creation_input_tokens": 90,
                                        "output_tokens": 5})

    def asst(mid):
        return sdk.AssistantMessage(content=[sdk.TextBlock(text="t")], model="m",
                                    message_id=mid,
                                    usage={"input_tokens": 1000, "output_tokens": 1})

    def started():
        return sdk.TaskStartedMessage(subtype="task_started", data={}, task_id="T1",
                                      description="modeler", uuid="u", session_id="s")

    def done():
        return sdk.TaskNotificationMessage(subtype="task_notification", data={},
                                           task_id="T1", status="completed",
                                           output_file="o", summary="s", uuid="u2",
                                           session_id="s")

    stream = [asst("m1"), asst("m1"), started(), result(2, 0.1), done(), asst("m2"),
              result(4, 0.3), asst("never")]
    seen = {}

    class FakeClient:
        def __init__(self, options):
            seen["env"] = options.env

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def query(self, prompt):
            pass

        async def receive_messages(self):
            for m in stream:
                seen.setdefault("n", 0)
                seen["n"] += 1
                yield m

    monkeypatch.setattr(sdk, "ClaudeSDKClient", FakeClient)
    res = backend.run(run, model="claude-sonnet-5-5")
    assert res.ok and seen["n"] == 7  # stopped at the second result, not the first
    assert res.efficiency["turns"] == 4 and res.efficiency["cost_usd"] == 0.3
    assert res.efficiency["tokens_in"] == 100  # session totals from the result
    assert res.details["live_tokens"] == (2000, 2)  # m1 counted once, m2 once
    assert seen["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
