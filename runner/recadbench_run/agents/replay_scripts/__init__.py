"""Scripted tool-call sequences for the replay agent backend (no model).

A script is a generator ``steps(ctx)`` that yields :class:`Step` objects and receives each
result back (``result = yield step``), so later steps may depend on earlier tool output. Every
step goes through the same dispatcher and gate as a model's tool calls. ``expect`` marks steps
whose gate decision is part of the test (``"deny"`` / ``"allow"``); a mismatch fails the replay.

Built-in scripts (``--script <name>``): ``oracle_build123d`` (Track T oracle with the build123d
skillset: read the seeded plan, write cad/part.py through the skillset contract, run it in the
MCP session, validate). A JSON file ``[{"tool": ..., "args": {...}, "expect": ...}, ...]`` is a
static script.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Step:
    tool: str
    args: dict = field(default_factory=dict)
    expect: str | None = None  # "allow" | "deny" | None
    note: str = ""


def oracle_build123d(ctx: dict) -> Generator[Step, object, None]:
    from .ir_build123d import write_part_py

    run = Path(ctx["run_dir"])
    res = yield Step("Read", {"file_path": "input/task.yaml"})
    res = yield Step("Read", {"file_path": "plan/re_plan.v1.json"})
    plan = json.loads((run / "plan" / "re_plan.v1.json").read_text(encoding="utf-8"))
    # the gate keeps the CAD program locked for anything but the plan's own rules; the scan is
    # never needed by the oracle (a denied probe shows the deny list is live)
    yield Step("mcp__build123d__import_cad_file", {"path": "input/input.stl"}, expect="deny",
               note="scan import is denied (ADR 0004)")
    src, skipped = write_part_py(plan)
    lines = ["# oracle analysis (replay agent)", "", "| id | type |", "|---|---|"]
    lines += [f"| {f['id']} | {f['type']} |" for f in plan["features"]]
    if skipped:
        lines += ["", "Not built:", *[f"- {s}" for s in skipped]]
    yield Step("Write", {"file_path": "plan/analysis.md", "content": "\n".join(lines) + "\n"})
    yield Step("Write", {"file_path": "cad/part.py", "content": src})
    res = yield Step("mcp__build123d__execute_file",
                     {"path": str(run / "cad" / "part.py"), "result_name": "part"},
                     expect="allow")
    yield Step("mcp__build123d__validate", {})
    yield Step("mcp__build123d__measure", {})
    del res


SCRIPTS = {"oracle_build123d": oracle_build123d}


def load(name_or_path: str):
    if name_or_path in SCRIPTS:
        return SCRIPTS[name_or_path]
    p = Path(name_or_path)
    if p.is_file():
        steps = json.loads(p.read_text(encoding="utf-8"))

        def static(ctx: dict):
            for s in steps:
                yield Step(s["tool"], s.get("args") or {}, s.get("expect"), s.get("note", ""))

        return static
    raise KeyError(f"unknown replay script {name_or_path!r} (built-in: {', '.join(SCRIPTS)})")
