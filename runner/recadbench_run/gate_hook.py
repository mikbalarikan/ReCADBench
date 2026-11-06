"""The run gate (00_DECISIONS D6, ADR 0004): one check for every tool call of an agent.

Used three ways, all through :meth:`Gate.check` / :meth:`Gate.commit`:

* Claude Code PreToolUse hook (interactive sessions; ``.claude/settings.json`` written by
  compose): ``python -m recadbench_run.gate_hook --run-dir <run>`` reads the hook JSON on stdin
  and prints a ``permissionDecision``;
* Claude Agent SDK: :func:`sdk_pre_tool_use` is registered as an in-process PreToolUse hook;
* the LiteLLM loop and the replay backend call :meth:`Gate.check` before dispatching each tool.

Rules (the reason text of a denial tells the agent what to do):

1. **Shell is denied**, except a command that matches the backend's ``allowed_commands``
   (``backend.yaml``; NX: ``recadbench nx-run cad/*.py``, plus the runner's documented
   ``--timeout S`` flag). No shell metacharacters, no ``..``.
2. **MCP tools**: ``mcp__retk__<t>`` only for the strategy's tool subset (intersected with the
   harness level); the L1 backend's tools (``cad_prefixes.all``); every other MCP tool is denied.
3. **denied_tools** (``backend.yaml``, ADR 0004): name prefix + ``re.search(args_regex,
   json.dumps(arguments))``. For ``kind: cli`` the arguments are ``{"command", "journal"}`` with
   the journal SOURCE from ``nx_run.journal_source`` (comments removed), exactly as ``recadbench
   nx-run`` matches it, and the same check runs over every ``.py`` the journal can import (run
   dir, ``lib/``). A script an MCP tool runs by path (build123d ``execute_file``) is matched as the
   code tool it stands for (``gate_policy.yaml: body_as``), and so are the ``.py`` files under
   ``cad/`` and ``lib/`` it can import.
4. **Scan-code patterns** (``gate_policy.yaml: scan_code_patterns``): code that imports a mesh
   library or an OCP mesh reader, loads a mesh file or touches ``input/`` is denied, whatever the
   backend.
5. **Paths**: every path argument (file tools, and any absolute path in any tool's arguments) must
   lie inside the run dir; writes never go to protected paths (``input/``, ``lib/``, ``.claude/``,
   the manifests, ...), and plan revisions that were seeded or already executed are read-only.
6. **Plan before CAD**: any backend tool / allowed command needs a schema-valid
   ``plan/re_plan.vN.json`` (contiguous revisions 1..N, ``revision`` = N, the run's part id).
7. **Revision rule**: a call that executes the CAD source (``cad/*.py`` for build123d / NX,
   ``gate_policy.yaml: source_tools``) records (N, fingerprint of the CAD sources). If the sources
   changed since revision N was executed, the next such call needs revision N+1 (a CAD-side change
   is a plan change: oracle writes an unchanged copy with a change_log line).

State lives in ``.recadbench/gate_state.json`` (written only by the gate; the agent cannot write
``.recadbench/``). Every decision is appended to ``.recadbench/gate_log.jsonl``.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

STATE_DIR = ".recadbench"
GATE_FILE = f"{STATE_DIR}/gate.json"
STATE_FILE = f"{STATE_DIR}/gate_state.json"
LOG_FILE = f"{STATE_DIR}/gate_log.jsonl"
BACKEND_FILE = f"{STATE_DIR}/backend.yaml"
PLAN_RE = re.compile(r"^re_plan\.v([0-9]+)\.json$")
SHELL_META = re.compile(r"[;&|<>`$(){}\[\]*?!~\n\r\\\"']")
OPAQUE_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".zip", ".egg", ".whl", ".pyz")
_ABS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


@dataclass
class Decision:
    allow: bool
    reason: str = ""
    category: str = ""
    record: dict | None = None  # state update to commit when the call goes ahead

    def to_json(self) -> dict:
        return {"allow": self.allow, "reason": self.reason, "category": self.category}


@dataclass
class PlanStatus:
    revision: int = 0
    ok: bool = False
    reason: str = ""
    files: dict[int, Path] = field(default_factory=dict)


# ------------------------------------------------------------------------------ helpers
def load_policy() -> dict:
    import yaml

    return yaml.safe_load((Path(__file__).with_name("gate_policy.yaml")).read_text("utf-8"))


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def inside(path: Path, root: Path) -> bool:
    try:
        rp = _norm(path.resolve())
    except OSError:
        rp = _norm(path)
    r = _norm(root.resolve())
    return rp == r or rp.startswith(r.rstrip("\\/") + os.sep)


def _load_nx_run():
    """backends/nx/runner/nx_run.py (stdlib only), the module ``recadbench nx-run`` runs."""
    from recad_cli.recadbench import _load_nx_run as load

    return load()


def strip_comments_source(raw: bytes) -> str:
    """The code text the matchers see: ``nx_run.journal_source`` (PEP 263 decoding, ``#``
    comments removed; strings and docstrings kept)."""
    return _load_nx_run().journal_source(raw)


def denied_match(rules: list[dict], tool: str, args: dict) -> dict | None:
    """ADR 0004 matcher: ``tool`` prefix and optional ``args_regex`` on ``json.dumps(args)``."""
    blob = json.dumps(args, ensure_ascii=False)
    for r in rules or []:
        if tool.startswith(r["tool"]) and ("args_regex" not in r
                                           or re.search(r["args_regex"], blob)):
            return r
    return None


def plan_status(run_dir: Path, part_id: str | None) -> PlanStatus:
    from recadbench_spec import validate_document

    st = PlanStatus()
    d = run_dir / "plan"
    if d.is_dir():
        for p in d.iterdir():
            m = PLAN_RE.match(p.name)
            if m and p.is_file():
                st.files[int(m.group(1))] = p
    if not st.files:
        st.reason = "no plan/re_plan.v1.json yet: write the plan before any CAD call"
        return st
    n = max(st.files)
    st.revision = n
    missing = [k for k in range(1, n + 1) if k not in st.files]
    if missing:
        st.reason = f"plan revisions must be contiguous: missing v{missing[0]}"
        return st
    path = st.files[n]
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        st.reason = f"plan/{path.name} is not readable JSON: {exc}"
        return st
    rep = validate_document(doc, "re_plan_ir", source=path)
    if not rep.ok:
        errs = "; ".join(f"{e.path} {e.message}" for e in rep.errors[:4])
        st.reason = f"plan/{path.name} is not schema-valid: {errs}"
        return st
    if part_id and doc.get("part_id") != part_id:
        st.reason = f"plan/{path.name} part_id {doc.get('part_id')!r} != {part_id!r}"
        return st
    st.ok = True
    return st


def source_fingerprint(run_dir: Path, globs: list[str]) -> str:
    """sha256 over the CAD source files below cad/ (path + content), generated files excluded."""
    h = hashlib.sha256()
    cad = run_dir / "cad"
    if cad.is_dir():
        for p in sorted(cad.rglob("*")):
            rel = p.relative_to(cad).as_posix()
            if not p.is_file() or "logs" in p.relative_to(cad).parts or "__pycache__" in rel:
                continue
            if any(fnmatch.fnmatch(p.name, g) for g in globs):
                h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------------- gate
class Gate:
    """Gate of one run dir. ``config`` defaults to ``<run>/.recadbench/gate.json``."""

    def __init__(self, run_dir: str | Path, config: dict | None = None,
                 backend_manifest: dict | None = None, policy: dict | None = None):
        self.run_dir = Path(run_dir).resolve()
        self.cfg = config if config is not None else json.loads(
            (self.run_dir / GATE_FILE).read_text(encoding="utf-8"))
        self.policy = policy or self.cfg.get("policy") or load_policy()
        if backend_manifest is None and (self.run_dir / BACKEND_FILE).is_file():
            import yaml

            backend_manifest = yaml.safe_load((self.run_dir / BACKEND_FILE).read_text("utf-8"))
        self.backend = backend_manifest or {}
        iface = self.backend.get("interface") or {}
        self.kind = iface.get("kind", "none")
        self.cad_prefixes = list((iface.get("cad_prefixes") or {}).get("all") or [])
        self.allowed_commands = list(iface.get("allowed_commands") or [])
        self.denied_rules = list(self.backend.get("denied_tools") or [])
        self.bpol = (self.policy.get("backends") or {}).get(self.backend.get("id", ""), {})
        self.scan_patterns = [re.compile(p) for p in self.policy.get("scan_code_patterns") or []]

    # -- state
    def state(self) -> dict:
        p = self.run_dir / STATE_FILE
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"executed": None, "history": []}

    def commit(self, d: Decision) -> None:
        if not (d.allow and d.record):
            return
        st = self.state()
        st["executed"] = d.record
        st.setdefault("history", []).append(d.record)
        p = self.run_dir / STATE_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, p)

    def log(self, tool: str, d: Decision) -> None:
        p = self.run_dir / LOG_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"t": round(time.time(), 3), "tool": tool, **d.to_json()}) + "\n")

    def __call__(self, tool: str, args: dict | None) -> Decision:
        d = self.check(tool, args)
        self.commit(d)
        with contextlib.suppress(OSError):
            self.log(tool, d)
        return d

    # -- the check
    def check(self, tool: str, args: dict | None) -> Decision:
        args = args if isinstance(args, dict) else {}
        try:
            if args.get("run_in_background"):
                return Decision(False, "background tasks are not available in benchmark runs: "
                                "run it in the foreground and wait for it", "background")
            if tool in (self.policy.get("shell_tools") or []):
                return self._shell(tool, args)
            bad = self._foreign_paths(args)
            if bad:
                return Decision(False, f"path outside the run dir: {bad}", "path")
            if tool.startswith("mcp__"):
                return self._mcp(tool, args)
            return self._builtin(tool, args)
        except Exception as exc:  # fail closed
            return Decision(False, f"gate error ({type(exc).__name__}: {exc})", "error")

    # paths -------------------------------------------------------------------------------
    def _resolve(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.run_dir / q

    def _foreign_paths(self, args: dict) -> str | None:
        """Any absolute path (or ``..`` escape) in any argument string must stay in the run."""
        def walk(v: Any):
            if isinstance(v, dict):
                for x in v.values():
                    yield from walk(x)
            elif isinstance(v, list):
                for x in v:
                    yield from walk(x)
            elif isinstance(v, str):
                yield v

        for s in walk(args):
            if len(s) > 4096 or "\n" in s:
                continue  # code / content, not a path
            if _ABS_PATH.match(s) or s.startswith("~") or ".." in PurePosixPath(
                    s.replace("\\", "/")).parts:
                if s.startswith("~") or not inside(self._resolve(s.split("*")[0] or "."),
                                                   self.run_dir):
                    return s
        return None

    def _rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.run_dir).as_posix()
        except ValueError:
            return p.as_posix()

    def _builtin(self, tool: str, args: dict) -> Decision:
        if tool not in (self.policy.get("builtin_allowed") or []):
            return Decision(False, f"tool {tool} is not available in benchmark runs", "denied")
        fkeys = (self.policy.get("file_tools") or {}).get(tool) or []
        for k in fkeys:
            v = args.get(k)
            if isinstance(v, str) and v:
                if not inside(self._resolve(v.split("*")[0] or "."), self.run_dir):
                    return Decision(False, f"{tool}: {v} is outside the run dir", "path")
        if tool in (self.policy.get("write_tools") or []):
            target = args.get("file_path")
            if not isinstance(target, str) or not target:
                return Decision(False, f"{tool}: file_path missing", "path")
            rel = self._rel(self._resolve(target))
            for prot in self.policy.get("protected") or []:
                if rel == prot.rstrip("/") or (prot.endswith("/") and rel.startswith(prot)):
                    return Decision(False, f"{rel} is read-only in a benchmark run", "path")
            m = PLAN_RE.match(PurePosixPath(rel).name)
            if m and rel.startswith("plan/"):
                k = int(m.group(1))
                ex = (self.state().get("executed") or {}).get("revision", 0)
                if k in (self.cfg.get("seeded_revisions") or []):
                    return Decision(False, f"plan/{PurePosixPath(rel).name} was given by the "
                                    "harness and is read-only; write the next revision", "plan")
                if k <= ex:
                    return Decision(False, f"plan revision v{k} was already executed and is "
                                    f"read-only; write v{ex + 1}", "plan")
        return Decision(True, "", "builtin")

    # MCP -----------------------------------------------------------------------------------
    def _mcp(self, tool: str, args: dict) -> Decision:
        if tool.startswith("mcp__retk__"):
            name = tool[len("mcp__retk__"):]
            if name in (self.cfg.get("retk_tools") or []):
                return Decision(True, "", "retk")
            return Decision(False, f"retk tool {name} is not part of this strategy / harness "
                            "level", "retk")
        if not any(tool.startswith(p) for p in self.cad_prefixes):
            return Decision(False, f"MCP tool {tool} is not part of this run", "denied")
        hit = denied_match(self.denied_rules, tool, args)
        if hit:
            return Decision(False, f"denied: {hit.get('reason', 'backend.yaml denied_tools')} "
                            "(scan analysis goes through retk only)", "denied_tool")
        code_arg = (self.bpol.get("code_args") or {}).get(tool)
        if code_arg and isinstance(args.get(code_arg), str):
            why = self._scan_code(strip_comments_source(args[code_arg].encode("utf-8")))
            if why:
                return Decision(False, f"denied: code {why}", "scan_code")
        body = (self.bpol.get("body_as") or {}).get(tool)
        if body:
            why = self._script_body(args.get(body["path_arg"]), body)
            if why:
                return Decision(False, why, "scan_code")
        src = (self.bpol.get("source_tools") or {}).get(tool)
        return self._cad(tool, f"{tool} {json.dumps(args, sort_keys=True)[:200]}",
                         source_call=bool(src))

    def _script_body(self, path: Any, body: dict) -> str | None:
        if not isinstance(path, str) or not path:
            return f"{body['path_arg']} missing"
        p = self._resolve(path)
        if not inside(p, self.run_dir):
            return f"{path} is outside the run dir"
        if not p.is_file():
            return f"{path} does not exist"
        files = [p] + self._importable()
        for f in files:
            if f.suffix.lower() in OPAQUE_SUFFIXES:
                return f"denied: {self._rel(f)}: compiled or packaged module"
            if f.suffix.lower() not in (".py", ".pyw") and f != p:
                continue
            text = strip_comments_source(f.read_bytes())
            hit = denied_match(self.denied_rules, body["as_tool"], {body["code_arg"]: text})
            if hit:
                return f"denied: {self._rel(f)}: {hit.get('reason', 'denied_tools')}"
            why = self._scan_code(text)
            if why:
                return f"denied: {self._rel(f)} {why}"
        return None

    def _importable(self) -> list[Path]:
        out = []
        for d in ("cad", "lib"):
            root = self.run_dir / d
            if root.is_dir():
                out += [p for p in sorted(root.rglob("*")) if p.is_file()
                        and "__pycache__" not in p.parts
                        and p.suffix.lower() in (".py", ".pyw") + OPAQUE_SUFFIXES]
        return out

    def _scan_code(self, text: str) -> str | None:
        for rx in self.scan_patterns:
            m = rx.search(text)
            if m:
                return f"reads or analyses a mesh / the scan ({m.group(0)[:60]!r}); scan " \
                       "analysis goes through retk only"
        return None

    # shell ---------------------------------------------------------------------------------
    def _shell(self, tool: str, args: dict) -> Decision:
        cmd = args.get("command")
        if self.kind != "cli" or tool not in ("Bash", "PowerShell") or not isinstance(cmd, str):
            return Decision(False, "shell is not available in benchmark runs (only the "
                            "backend's allowed commands, and this backend has none)", "shell")
        cmd = cmd.strip()
        if SHELL_META.search(cmd):
            return Decision(False, "shell metacharacters are not allowed; run exactly "
                            f"{' | '.join(self.allowed_commands)}", "shell")
        toks = shlex.split(cmd)
        if len(toks) < 3 or ".." in PurePosixPath(toks[2]).parts:
            return Decision(False, f"only {' | '.join(self.allowed_commands)} may run", "shell")
        base = " ".join(toks[:3])
        if not any(fnmatch.fnmatchcase(base, pat) for pat in self.allowed_commands):
            return Decision(False, f"only {' | '.join(self.allowed_commands)} may run", "shell")
        rest = toks[3:]
        flags = set(self.bpol.get("nx_run_flags") or [])
        while rest:
            if len(rest) < 2 or rest[0] not in flags or not re.fullmatch(r"[0-9]+", rest[1]):
                return Decision(False, f"unsupported arguments {' '.join(rest)!r} (allowed: "
                                f"{', '.join(sorted(flags)) or 'none'} with a number)", "shell")
            rest = rest[2:]
        journal = self.run_dir / toks[2]
        if not inside(journal, self.run_dir / "cad") or not journal.is_file():
            return Decision(False, f"{toks[2]} is not a journal under cad/", "shell")
        why = self._journals(journal, base)
        if why:
            return Decision(False, why, "denied_tool")
        return self._cad(tool, base, source_call=True)

    def _journals(self, journal: Path, command: str) -> str | None:
        """nx-run's own check (journal + every .py of the run dir and lib/), on the run's
        backend.yaml rules, plus the scan-code patterns."""
        files = [journal]
        for p in sorted(self.run_dir.rglob("*")):
            if STATE_DIR in p.relative_to(self.run_dir).parts or "__pycache__" in p.parts:
                continue
            if p.suffix.lower() in OPAQUE_SUFFIXES:
                return f"denied: {self._rel(p)}: compiled or packaged module"
            if p.suffix.lower() in (".py", ".pyw") and p.is_file() and p != journal:
                files.append(p)
        for f in files:
            text = strip_comments_source(f.read_bytes())
            hit = denied_match(self.denied_rules, command, {"command": command, "journal": text})
            if hit:
                return f"denied: {self._rel(f)}: {hit.get('reason', 'denied_tools')}"
            why = self._scan_code(text)
            if why:
                return f"denied: {self._rel(f)} {why}"
        return None

    # plan + revision -----------------------------------------------------------------------
    def _cad(self, tool: str, label: str, source_call: bool) -> Decision:
        ps = plan_status(self.run_dir, self.cfg.get("part_id"))
        if not ps.ok:
            return Decision(False, f"CAD is locked: {ps.reason}", "plan")
        if not source_call:
            return Decision(True, "", "cad")
        fp = source_fingerprint(self.run_dir, self.bpol.get("source_globs") or ["*.py"])
        ex = self.state().get("executed")
        if ex and ps.revision <= ex["revision"] and fp != ex["fingerprint"]:
            return Decision(False, f"the CAD source changed after plan revision v{ex['revision']}"
                            f" was executed: write plan/re_plan.v{ex['revision'] + 1}.json "
                            "(a change_log line naming the change) before executing again",
                            "revision")
        return Decision(True, "", "cad", {"revision": ps.revision, "fingerprint": fp,
                                          "call": label, "t": round(time.time(), 3)})


# ------------------------------------------------------------------------ hook adapters
def hook_output(d: Decision) -> dict:
    """PreToolUse hook JSON (Claude Code / Agent SDK)."""
    if d.allow:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": d.reason}}


def make_sdk_hook(gate: Gate, on_decision=None):
    """An async PreToolUse callback for ``claude_agent_sdk.HookMatcher``."""

    async def pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        d = gate(input_data.get("tool_name", ""), input_data.get("tool_input") or {})
        if on_decision is not None:
            on_decision(input_data.get("tool_name", ""), d)
        return hook_output(d)

    return pre_tool_use


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recadbench gate", description="PreToolUse gate hook")
    ap.add_argument("--run-dir", default=None, help="run dir (default: hook cwd / "
                    "CLAUDE_PROJECT_DIR)")
    a = ap.parse_args(argv)
    raw = sys.stdin.read().lstrip("﻿")  # PowerShell pipes may prepend a BOM
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        print("gate: unreadable hook input", file=sys.stderr)
        return 2
    run = a.run_dir or os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or "."
    try:
        gate = Gate(run)
    except Exception as exc:  # fail closed
        print(f"gate: cannot load the run's gate config: {exc}", file=sys.stderr)
        return 2
    d = gate(data.get("tool_name", ""), data.get("tool_input") or {})
    sys.stdout.write(json.dumps(hook_output(d)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
