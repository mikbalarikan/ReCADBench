"""backend.yaml `cad_prefixes` vs the MCP `tools/list` snapshots (backends/<b>/mcp_tools.json).

CI-safe: the checked-in snapshots agree with backend.yaml, the checker catches known defects in
synthetic snapshots, and the stdio client works against a fake MCP server.
Host-only (`test_live_tools_match_snapshot`): starts each installed MCP server, lists its tools
and compares them with the snapshot; skips when the server is not installed
(RECAD_BUILD123D_MCP / RECAD_SOLIDCRAFT_CLI / RECAD_FREECAD_MCP + RECAD_FREECAD_CMD +
RECAD_FREECAD_BRIDGE, see backends/conformance/mcp_tools.py). Refresh a snapshot with
`python backends/conformance/mcp_tools.py refresh --backend <b>` after a pin bump.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

BACKENDS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("mcp_tools", BACKENDS / "conformance" /
                                               "mcp_tools.py")
mcp_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_tools)


def snapshot(backend: str) -> dict:
    return json.loads(mcp_tools.snapshot_path(backend).read_text(encoding="utf-8"))


@pytest.mark.parametrize("backend", mcp_tools.MCP_BACKENDS)
def test_snapshot_matches_backend_yaml(backend):
    snap = snapshot(backend)
    assert snap["backend"] == backend
    assert snap["tools"], "empty snapshot"
    assert mcp_tools.check(backend, snap) == []


@pytest.mark.parametrize("backend", mcp_tools.MCP_BACKENDS)
def test_snapshot_pin_matches_backend_yaml(backend):
    sw = mcp_tools.load_backend(backend)["software"]
    assert snapshot(backend)["pin"] == f"{sw['name']} {sw['version']}"


@pytest.mark.parametrize("backend", mcp_tools.MCP_BACKENDS)
def test_snapshot_is_lf_ascii(backend):
    raw = mcp_tools.snapshot_path(backend).read_bytes()
    assert b"\r\n" not in raw
    raw.decode("ascii")


def _synthetic():
    return copy.deepcopy(snapshot("solidcraft"))


def test_check_flags_stale_mutating_entry():
    snap = _synthetic()
    snap["tools"] = [t for t in snap["tools"] if t["name"] != "set_parameter"]
    problems = mcp_tools.check("solidcraft", snap)
    assert any("mcp__solidcraft__set_parameter" in p and "matches no tool" in p
               for p in problems)


def test_check_flags_new_unannotated_tool():
    snap = _synthetic()
    snap["tools"].append({"name": "delete_everything", "read_only_hint": None})
    problems = mcp_tools.check("solidcraft", snap)
    assert problems == [p for p in problems if "delete_everything" in p] and len(problems) == 1


def test_check_accepts_new_read_only_tool():
    snap = _synthetic()
    snap["tools"].append({"name": "describe", "read_only_hint": True})
    assert mcp_tools.check("solidcraft", snap) == []


def test_check_flags_stale_not_mutating_entry():
    snap = _synthetic()
    snap["not_mutating"]["gone_tool"] = "reason"
    assert any("gone_tool" in p for p in mcp_tools.check("solidcraft", snap))


def test_check_flags_tool_both_mutating_and_excluded():
    snap = _synthetic()
    snap["not_mutating"]["execute"] = "wrong"
    assert any("in mutating and in not_mutating" in p
               for p in mcp_tools.check("solidcraft", snap))


def test_prefix_entries_match_by_prefix():
    assert mcp_tools.matches("mcp__freecad__create_box", "mcp__freecad__create_")
    assert not mcp_tools.matches("mcp__freecad__recompute_document", "mcp__freecad__recompute")


def test_conditional_entry_is_accepted_only_when_listed():
    snap = copy.deepcopy(snapshot("build123d"))
    assert "load_part" not in {t["name"] for t in snap["tools"]}
    assert mcp_tools.check("build123d", snap) == []
    snap["conditional"] = {}
    assert any("load_part" in p for p in mcp_tools.check("build123d", snap))


FAKE_SERVER = textwrap.dedent('''
    import json, sys
    print("log line on stdout", flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                   "serverInfo": {"name": "fake", "version": "9.9"}}
        elif msg["method"] == "tools/list":
            if (msg.get("params") or {}).get("cursor") == "p2":
                res = {"tools": [{"name": "b_tool", "inputSchema": {}}]}
            else:
                res = {"tools": [{"name": "a_tool", "inputSchema": {},
                                  "annotations": {"readOnlyHint": True}}],
                       "nextCursor": "p2"}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
''')


def test_list_tools_against_fake_server(tmp_path):
    server = tmp_path / "fake_server.py"
    server.write_text(FAKE_SERVER, encoding="utf-8")
    live = mcp_tools.list_tools([sys.executable, str(server)], timeout=30)
    assert live == {"server": {"name": "fake", "version": "9.9"},
                    "tools": [{"name": "a_tool", "read_only_hint": True},
                              {"name": "b_tool", "read_only_hint": None}]}


def _available(backend: str) -> bool:
    if mcp_tools.server_command(backend) is None:
        return False
    if backend == "freecad":  # 0.6.1 exits without a bridge; refresh starts a private one
        return bool(os.environ.get("RECAD_FREECAD_CMD") and os.environ.get("RECAD_FREECAD_BRIDGE"))
    return True


@pytest.mark.parametrize("backend", mcp_tools.MCP_BACKENDS)
def test_live_tools_match_snapshot(backend, tmp_path):
    """Host-only: the installed server's live tools/list equals the checked-in snapshot."""
    if not _available(backend):
        pytest.skip(f"{backend} MCP server not installed on this host")
    out = tmp_path / f"{backend}.json"
    out.write_bytes(mcp_tools.snapshot_path(backend).read_bytes())  # keeps hand-maintained maps
    live = mcp_tools.refresh(backend, out)
    snap = snapshot(backend)
    assert live["server"] == snap["server"]
    assert live["tools"] == snap["tools"]
    assert mcp_tools.check(backend, live) == []
