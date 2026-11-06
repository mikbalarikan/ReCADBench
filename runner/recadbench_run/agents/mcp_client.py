"""Minimal synchronous MCP stdio client (JSON-RPC 2.0, newline-delimited; stdlib only).

Used by the LiteLLM loop and the replay backend, which dispatch tools themselves (the Claude
Agent SDK talks to the servers through Claude Code). Same wire protocol as
``backends/conformance/mcp_tools.py``; adds ``tools/call`` and answers server requests (ping)
so a server never blocks on the client.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path

PROTOCOL = "2025-06-18"


class McpError(RuntimeError):
    pass


class McpServer:
    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, cwd: str | Path | None = None,
                 timeout: float = 600.0):
        self.name = name
        self.timeout = timeout
        self._id = 0
        self._lines: queue.Queue[bytes] = queue.Queue()
        self.proc = subprocess.Popen([command, *(args or [])], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=env, cwd=str(cwd) if cwd else None)
        threading.Thread(target=self._pump, daemon=True).start()
        self._lock = threading.Lock()
        init = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                           "clientInfo": {"name": "recadbench-run",
                                                          "version": "1"}}, timeout=120)
        self.server_info = init.get("serverInfo") or {}
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._tools: list[dict] | None = None

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            self._lines.put(raw)
        self._lines.put(b"")

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            while True:
                try:
                    raw = self._lines.get(timeout=timeout or self.timeout)
                except queue.Empty as exc:
                    raise McpError(f"{self.name}: no response to {method} in "
                                   f"{timeout or self.timeout:g} s") from exc
                if not raw:
                    raise McpError(f"{self.name}: server closed stdout")
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:  # a server request (ping, roots/list)
                    result = {} if msg["method"] == "ping" else None
                    reply = {"jsonrpc": "2.0", "id": msg["id"]}
                    if result is None:
                        reply["error"] = {"code": -32601, "message": "not supported"}
                    else:
                        reply["result"] = result
                    self._send(reply)
                    continue
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise McpError(f"{self.name}: {method} failed: {msg['error']}")
                    return msg.get("result") or {}

    def tools(self) -> list[dict]:
        if self._tools is None:
            out, cursor = [], None
            while True:
                page = self.request("tools/list", {"cursor": cursor} if cursor else {})
                out += page.get("tools") or []
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            self._tools = out
        return self._tools

    def call(self, tool: str, arguments: dict) -> tuple[bool, str]:
        """``(ok, text)``: the text blocks of the result joined; ok = not isError."""
        res = self.request("tools/call", {"name": tool, "arguments": arguments})
        texts = []
        for block in res.get("content") or []:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            else:
                texts.append(f"[{block.get('type')} content omitted]")
        if not texts and res.get("structuredContent") is not None:
            texts.append(json.dumps(res["structuredContent"]))
        return not res.get("isError", False), "\n".join(texts)

    def close(self) -> None:
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
