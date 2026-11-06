#!/usr/bin/env python3
"""Snapshot the live MCP `tools/list` of each MCP backend and check it against backend.yaml.

    python backends/conformance/mcp_tools.py refresh [--backend b ...] [--out-dir DIR]
    python backends/conformance/mcp_tools.py check   [--backend b ...]

`refresh` starts the backend's pinned MCP server over stdio (newline-delimited JSON-RPC, no MCP
client library needed), sends initialize + tools/list, and writes `backends/<b>/mcp_tools.json`
(or `<out-dir>/<b>.json`): server name/version as reported by the server, and per tool its name
and `readOnlyHint` annotation. Tool descriptions and schemas are not stored (they change with
wording, not with the gate surface).

`check` compares a snapshot with `backend.yaml` `interface.cad_prefixes` (no server needed):

1. every `mutating` entry (a full tool name or a prefix ending in `_`) matches at least one tool
   of the snapshot (no stale names);
2. every snapshot tool covered by `mutating` exists;
3. every tool whose `readOnlyHint` is not true is either covered by `mutating` or listed in the
   snapshot's hand-maintained `not_mutating` map with a reason (writes files only, ...);
4. every tool name starts with one of `cad_prefixes.all` once the server prefix is applied.

A `mutating` entry for a tool the server only serves with non-default flags (build123d-mcp
`load_part` needs `--library`) is listed in the snapshot's hand-maintained `conditional` map
with the reason; rule 1 accepts it. Both hand-maintained maps survive `refresh`.

Servers (environment overrides, else the backend.yaml `interface.mcp.command` on PATH):
  build123d   RECAD_BUILD123D_MCP   build123d-mcp
  solidcraft  RECAD_SOLIDCRAFT_CLI  solidcraft-cli mcp --headless
  freecad     RECAD_FREECAD_MCP     freecad-mcp (freecad-robust-mcp)

freecad-robust-mcp 0.6.1 connects to its bridge during initialize and exits without one. With
RECAD_FREECAD_CMD (freecadcmd) and RECAD_FREECAD_BRIDGE (blocking_bridge.py at the pinned tag)
set, `refresh` starts a private bridge on free localhost ports for the call and stops it after;
otherwise a bridge must already listen on FREECAD_XMLRPC_PORT (default 9875).

Exit codes: 0 ok, 1 mismatch / server failure, 2 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

BACKENDS_DIR = Path(__file__).resolve().parent.parent
MCP_BACKENDS = ("build123d", "freecad", "solidcraft")
ENV_VAR = {"build123d": "RECAD_BUILD123D_MCP", "freecad": "RECAD_FREECAD_MCP",
           "solidcraft": "RECAD_SOLIDCRAFT_CLI"}
PROTOCOL = "2025-06-18"


def load_backend(backend: str) -> dict[str, Any]:
    return yaml.safe_load((BACKENDS_DIR / backend / "backend.yaml").read_text(encoding="utf-8"))


def snapshot_path(backend: str) -> Path:
    return BACKENDS_DIR / backend / "mcp_tools.json"


def server_command(backend: str) -> list[str] | None:
    """argv of the MCP server, or None if it is not installed on this host."""
    mcp = load_backend(backend)["interface"]["mcp"]
    exe = os.environ.get(ENV_VAR[backend]) or shutil.which(mcp["command"])
    if not exe or not (os.path.isfile(exe) or shutil.which(exe)):
        return None
    return [exe, *mcp.get("args", [])]


def server_env(backend: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update({k: str(v) for k, v in
                (load_backend(backend)["interface"]["mcp"].get("env") or {}).items()})
    return env


def list_tools(argv: list[str], env: dict[str, str] | None = None,
               timeout: float = 60.0) -> dict[str, Any]:
    """initialize + tools/list over stdio; returns {"server": {...}, "tools": [...]}."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env)
    lines: queue.Queue[bytes] = queue.Queue()

    def pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(b"")

    threading.Thread(target=pump, daemon=True).start()

    def send(msg: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_for(msg_id: int) -> dict[str, Any]:
        while True:
            try:
                raw = lines.get(timeout=timeout)
            except queue.Empty as exc:
                raise RuntimeError(f"no response to request {msg_id} in {timeout:g} s") from exc
            if not raw:
                raise RuntimeError("server closed stdout")
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # a log line on stdout
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"request {msg_id} failed: {msg['error']}")
                return msg["result"]

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                         "clientInfo": {"name": "recadbench-mcp-tools", "version": "1"}}})
        init = wait_for(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[dict[str, Any]] = []
        cursor = None
        req = 2
        while True:
            send({"jsonrpc": "2.0", "id": req, "method": "tools/list",
                  "params": {"cursor": cursor} if cursor else {}})
            page = wait_for(req)
            tools.extend(page.get("tools", []))
            cursor = page.get("nextCursor")
            req += 1
            if not cursor:
                break
    finally:
        try:
            assert proc.stdin is not None
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    info = init.get("serverInfo", {})
    return {
        "server": {"name": info.get("name"), "version": info.get("version")},
        "tools": sorted(({"name": t["name"],
                          "read_only_hint": (t.get("annotations") or {}).get("readOnlyHint")}
                         for t in tools), key=lambda t: t["name"]),
    }


def matches(tool: str, entry: str) -> bool:
    """A `mutating` entry is a full tool name, or a prefix when it ends in `_`."""
    return tool.startswith(entry) if entry.endswith("_") else tool == entry


def check(backend: str, snap: dict[str, Any]) -> list[str]:
    """Problems between backend.yaml cad_prefixes and a snapshot (empty list = consistent)."""
    prefixes = load_backend(backend)["interface"]["cad_prefixes"]
    server_prefix = f"mcp__{backend}__"
    names = [server_prefix + t["name"] for t in snap["tools"]]
    hints = {server_prefix + t["name"]: t.get("read_only_hint") for t in snap["tools"]}
    not_mut = {server_prefix + k: v for k, v in (snap.get("not_mutating") or {}).items()}
    mutating = prefixes.get("mutating") or []
    problems = []
    for n in names:
        if not any(n.startswith(p) for p in prefixes["all"]):
            problems.append(f"{n}: not under cad_prefixes.all {prefixes['all']}")
    conditional = {server_prefix + k for k in (snap.get("conditional") or {})}
    for entry in mutating:
        if entry in conditional:
            continue
        if not any(matches(n, entry) for n in names):
            problems.append(f"mutating entry {entry!r} matches no tool of the live server")
    for n in names:
        covered = any(matches(n, e) for e in mutating)
        if covered and n in not_mut:
            problems.append(f"{n}: in mutating and in not_mutating")
        if not covered and hints[n] is not True and n not in not_mut:
            problems.append(f"{n}: readOnlyHint={hints[n]!r} but not in cad_prefixes.mutating "
                            "(add it, or list it under not_mutating with a reason)")
    for n in not_mut:
        if n not in hints:
            problems.append(f"not_mutating entry {n!r} is not a tool of the live server")
    return problems


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def freecad_bridge(env: dict[str, str]):
    """Start a private headless RobustMCPBridge if RECAD_FREECAD_CMD/_BRIDGE are set."""
    cmd, bridge = os.environ.get("RECAD_FREECAD_CMD"), os.environ.get("RECAD_FREECAD_BRIDGE")
    if not (cmd and bridge):
        yield env
        return
    env = dict(env, FREECAD_XMLRPC_PORT=str(_free_port()), FREECAD_SOCKET_PORT=str(_free_port()))
    # A conda-forge freecadcmd.exe on Windows needs the env's DLL dirs on PATH (what `conda
    # activate` adds); without them it dies with 0xC0000135 (DLL not found).
    prefix = next((p for p in Path(cmd).resolve().parents if (p / "conda-meta").is_dir()), None)
    if prefix is not None and os.name == "nt":
        dll_dirs = [prefix, prefix / "Library" / "mingw-w64" / "bin",
                    prefix / "Library" / "usr" / "bin", prefix / "Library" / "bin",
                    prefix / "Scripts"]
        env["PATH"] = os.pathsep.join([*map(str, dll_dirs), env.get("PATH", "")])
    proc = subprocess.Popen([cmd, bridge], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env)
    try:
        port = int(env["FREECAD_XMLRPC_PORT"])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"bridge exited with {proc.returncode}")
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            raise RuntimeError("bridge did not listen within 120 s")
        yield env
    finally:
        proc.kill()
        proc.wait()


def refresh(backend: str, out: Path) -> dict[str, Any]:
    argv = server_command(backend)
    if argv is None:
        raise FileNotFoundError(f"{backend}: MCP server not installed (set {ENV_VAR[backend]})")
    env = server_env(backend)
    if backend == "freecad":
        with freecad_bridge(env) as benv:
            live = list_tools(argv, env=benv)
    else:
        live = list_tools(argv, env=env)
    old = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    sw = load_backend(backend)["software"]
    snap = {
        "backend": backend,
        "pin": f"{sw['name']} {sw['version']}",
        "command": [Path(argv[0]).name, *argv[1:]],
        **live,
        # hand-maintained: tools with readOnlyHint != true that do not change the model
        "not_mutating": {k: v for k, v in (old.get("not_mutating") or {}).items()
                         if any(t["name"] == k for t in live["tools"])},
        # hand-maintained: mutating tools served only with non-default server flags
        "conditional": old.get("conditional") or {},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(snap, indent=2, ensure_ascii=True) + "\n")
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("refresh", "check"))
    ap.add_argument("--backend", action="append", choices=MCP_BACKENDS)
    ap.add_argument("--out-dir", help="refresh: write <out-dir>/<b>.json instead of the snapshot")
    a = ap.parse_args(argv)
    rc = 0
    for b in a.backend or MCP_BACKENDS:
        if a.cmd == "refresh":
            out = Path(a.out_dir) / f"{b}.json" if a.out_dir else snapshot_path(b)
            try:
                snap = refresh(b, out)
            except (OSError, RuntimeError) as exc:
                print(f"{b}: SKIP/FAIL {exc}", file=sys.stderr)
                rc = 1
                continue
            print(f"{b}: {snap['server']['name']} {snap['server']['version']}, "
                  f"{len(snap['tools'])} tools -> {out}")
        else:
            path = snapshot_path(b)
            if not path.is_file():
                print(f"{b}: no snapshot {path}")
                rc = 1
                continue
            snap = json.loads(path.read_text(encoding="utf-8"))
            problems = check(b, snap)
            print(f"{b}: {len(snap['tools'])} tools, " + ("OK" if not problems else "MISMATCH"))
            for p in problems:
                print(f"  - {p}")
            rc = rc or (1 if problems else 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
