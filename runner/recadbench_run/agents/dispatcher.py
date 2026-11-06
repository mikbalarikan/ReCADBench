"""Tool dispatcher for agent backends that run their own tool loop (LiteLLM loop, replay).

Exposes the same tool surface a Claude Code session has in a run dir: the file tools (Read,
Write, Edit, Glob, Grep, TodoWrite), Bash (only for a backend's allowed command), and the MCP
servers of ``.mcp.json`` (``mcp__<server>__<tool>``). Every call goes through
:class:`recadbench_run.gate_hook.Gate` first; a denial is returned to the model as an error
result (and counted). Stdlib only, apart from the gate.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gate_hook import Gate
from .mcp_client import McpError, McpServer

MAX_READ_LINES = 2000
TEXT_LIMIT = 60_000

LOCAL_TOOLS: list[dict] = [
    {"name": "Read", "description": "Read a text file of the run dir (cat -n numbering).",
     "input_schema": {"type": "object", "required": ["file_path"], "properties": {
         "file_path": {"type": "string"}, "offset": {"type": "integer"},
         "limit": {"type": "integer"}}}},
    {"name": "Write", "description": "Write (create or replace) a text file in the run dir.",
     "input_schema": {"type": "object", "required": ["file_path", "content"], "properties": {
         "file_path": {"type": "string"}, "content": {"type": "string"}}}},
    {"name": "Edit", "description": "Replace an exact, unique string in a file.",
     "input_schema": {"type": "object", "required": ["file_path", "old_string", "new_string"],
                      "properties": {"file_path": {"type": "string"},
                                     "old_string": {"type": "string"},
                                     "new_string": {"type": "string"},
                                     "replace_all": {"type": "boolean"}}}},
    {"name": "Glob", "description": "List files matching a glob pattern (relative to the run).",
     "input_schema": {"type": "object", "required": ["pattern"], "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string"}}}},
    {"name": "Grep", "description": "Search file contents with a regular expression.",
     "input_schema": {"type": "object", "required": ["pattern"], "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string"},
         "glob": {"type": "string"}}}},
    {"name": "TodoWrite", "description": "Record a todo list (no effect on files).",
     "input_schema": {"type": "object", "properties": {"todos": {"type": "array"}}}},
]
BASH_TOOL = {"name": "Bash", "description": "Run the backend's allowed CAD command (nothing else).",
             "input_schema": {"type": "object", "required": ["command"], "properties": {
                 "command": {"type": "string"}, "timeout": {"type": "integer"}}}}


@dataclass
class ToolResult:
    ok: bool
    text: str
    denied: bool = False


class Dispatcher:
    def __init__(self, run_dir: str | Path, gate: Gate | None = None, tracker=None,
                 env: dict[str, str] | None = None, servers: dict[str, McpServer] | None = None,
                 start_servers: bool = True):
        self.run = Path(run_dir).resolve()
        self.gate = gate or Gate(self.run)
        self.tracker = tracker
        if env is None:
            from ..compose import agent_env

            env = agent_env(self.run)
        self.env = env
        self.servers: dict[str, McpServer] = dict(servers or {})
        self._cfg = json.loads((self.run / ".mcp.json").read_text("utf-8")).get(
            "mcpServers", {}) if (self.run / ".mcp.json").is_file() else {}
        if start_servers:
            for name, c in self._cfg.items():
                if name not in self.servers:
                    e = dict(self.env)
                    e.update(c.get("env") or {})
                    self.servers[name] = McpServer(name, c["command"], c.get("args") or [],
                                                   env=e, cwd=self.run)

    # schemas ---------------------------------------------------------------------------------
    def tool_schemas(self) -> list[dict]:
        tools = [dict(t) for t in LOCAL_TOOLS]
        if self.gate.kind == "cli":
            tools.append(dict(BASH_TOOL))
        for name, srv in self.servers.items():
            for t in srv.tools():
                tools.append({"name": f"mcp__{name}__{t['name']}",
                              "description": (t.get("description") or "")[:1024],
                              "input_schema": t.get("inputSchema") or {"type": "object"}})
        return tools

    # dispatch --------------------------------------------------------------------------------
    def call(self, name: str, args: dict | None) -> ToolResult:
        args = args if isinstance(args, dict) else {}
        d = self.gate(name, args)
        if not d.allow:
            if self.tracker is not None:
                self.tracker.gate_denials += 1
            return ToolResult(False, f"DENIED by the benchmark gate: {d.reason}", denied=True)
        try:
            if name.startswith("mcp__"):
                _, server, tool = name.split("__", 2)
                if server not in self.servers:
                    res = ToolResult(False, f"MCP server {server} is not running")
                else:
                    ok, text = self.servers[server].call(tool, args)
                    res = ToolResult(ok, text[:TEXT_LIMIT])
            else:
                fn = getattr(self, f"_t_{name}", None)
                res = fn(args) if fn else ToolResult(False, f"unknown tool {name}")
        except (OSError, ValueError, McpError, subprocess.SubprocessError, re.error) as exc:
            res = ToolResult(False, f"{type(exc).__name__}: {exc}")
        if not res.ok and self.tracker is not None:
            self.tracker.tool_errors += 1
        return res

    def close(self) -> None:
        for s in self.servers.values():
            s.close()

    # local tools -----------------------------------------------------------------------------
    def _p(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.run / q

    def _t_Read(self, a: dict) -> ToolResult:
        p = self._p(a["file_path"])
        raw = p.read_bytes()
        if b"\0" in raw[:4096]:
            return ToolResult(False, f"{a['file_path']} is a binary file")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        off = max(int(a.get("offset") or 1), 1)
        lim = int(a.get("limit") or MAX_READ_LINES)
        sel = lines[off - 1: off - 1 + lim]
        return ToolResult(True, "\n".join(f"{i:6d}\t{ln}" for i, ln in
                                          enumerate(sel, start=off))[:TEXT_LIMIT])

    def _t_Write(self, a: dict) -> ToolResult:
        p = self._p(a["file_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(a["content"], encoding="utf-8", newline="\n")
        return ToolResult(True, f"wrote {p.relative_to(self.run).as_posix()}")

    def _t_Edit(self, a: dict) -> ToolResult:
        p = self._p(a["file_path"])
        text = p.read_text(encoding="utf-8")
        old, new = a["old_string"], a["new_string"]
        n = text.count(old)
        if n == 0:
            return ToolResult(False, "old_string not found")
        if n > 1 and not a.get("replace_all"):
            return ToolResult(False, f"old_string found {n} times; make it unique or replace_all")
        p.write_text(text.replace(old, new) if a.get("replace_all") else text.replace(old, new, 1),
                     encoding="utf-8", newline="\n")
        return ToolResult(True, f"edited {p.relative_to(self.run).as_posix()}")

    def _t_Glob(self, a: dict) -> ToolResult:
        base = self._p(a.get("path") or ".")
        hits = sorted(q.relative_to(self.run).as_posix() for q in base.glob(a["pattern"])
                      if q.is_file())
        return ToolResult(True, "\n".join(hits[:500]) or "(no matches)")

    def _t_Grep(self, a: dict) -> ToolResult:
        rx = re.compile(a["pattern"])
        base = self._p(a.get("path") or ".")
        files = [base] if base.is_file() else sorted(q for q in base.rglob("*") if q.is_file())
        out = []
        for f in files:
            if a.get("glob") and not fnmatch.fnmatch(f.name, a["glob"]):
                continue
            if f.stat().st_size > 5 << 20:
                continue
            try:
                for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if rx.search(ln):
                        out.append(f"{f.relative_to(self.run).as_posix()}:{i}:{ln[:300]}")
            except UnicodeDecodeError:
                continue
            if len(out) >= 300:
                break
        return ToolResult(True, "\n".join(out) or "(no matches)")

    def _t_TodoWrite(self, a: dict) -> ToolResult:
        return ToolResult(True, "ok")

    def _t_Bash(self, a: dict) -> ToolResult:
        import shutil

        toks = shlex.split(a["command"])
        toks[0] = shutil.which(toks[0], path=self.env.get("PATH")) or toks[0]
        timeout = float(a.get("timeout") or 900)
        p = subprocess.run(toks, cwd=self.run, env=self.env, capture_output=True, text=True,
                           timeout=timeout)
        text = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip()
        return ToolResult(p.returncode == 0, f"exit {p.returncode}\n{text}"[:TEXT_LIMIT])


def result_payload(res: ToolResult) -> dict[str, Any]:
    return {"ok": res.ok, "denied": res.denied, "text": res.text}
