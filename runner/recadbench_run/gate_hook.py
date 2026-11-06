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

   Every tool of ``backend.yaml: cad_prefixes.mutating`` (``execute``, ``restore_snapshot``,
   ``edit_feature``, ...) is held by the same rule (it is denied while the sources differ from
   the executed revision), but only ``source_tools`` record an execution.
8. **Writes** (file tools and every MCP argument that names a written file,
   ``gate_policy.yaml: write_args``) go only to ``writable`` folders (``plan/``, ``cad/``,
   ``out/``, ``work/``), never to protected paths or executed / seeded plan revisions.
9. **Code literals**: a string literal in code that names a path outside the run dir, a
   protected run-dir path or the ground-truth store is denied (best effort).

State lives in ``.recadbench/gate_state.json`` (written only by the gate). Its sha256 is also
kept OUTSIDE the run dir (``RECADBENCH_GATE_DIGEST_DIR``, default
``<run parent>/.recadbench-gate``); a state that does not match (overwritten, deleted, forged)
marks the run as tampered: no further CAD execution, and ``finish`` refuses to extract.
Every decision is appended to ``.recadbench/gate_log.jsonl``. :meth:`Gate.finish_check` is
the check ``finish`` runs before it executes the native file.
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
# a drive-root-relative Windows path in a code literal (\Users\x), not a regex such as r"\d+"
_ROOTED = re.compile(r"^\\[\w .-]+(?:[\\/][\w .-]+)+[\\/]?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
DIGEST_ENV = "RECADBENCH_GATE_DIGEST_DIR"


def digest_path(run_dir: Path) -> Path:
    """Where the gate keeps the sha256 of the run's gate_state.json: outside the run dir (the
    gate denies every path there), one file per absolute run-dir path."""
    run = Path(run_dir).resolve()
    base = Path(os.environ[DIGEST_ENV]) if os.environ.get(DIGEST_ENV) else \
        run.parent / ".recadbench-gate"
    tag = hashlib.sha256(_norm(run).encode("utf-8")).hexdigest()[:16]
    return base / f"{run.name}-{tag}.sha256"


def clear_digest(run_dir: Path) -> None:
    """compose (and a forced re-run) start the state over."""
    with contextlib.suppress(OSError):
        digest_path(run_dir).unlink()


def _string_literals(text: str) -> list[str]:
    """Values of the plain string literals of Python source (f-strings: their literal parts)."""
    import ast
    import io
    import tokenize

    out: list[str] = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return [a or b for a, b in re.findall(r"""(?:"([^"\n]*)"|'([^'\n]*)')""", text)]
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    for t in toks:
        if t.type == tokenize.STRING:
            try:
                v = ast.literal_eval(t.string)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                v = t.string.strip("rRbBuUfF").strip("'\"")
            if isinstance(v, bytes):
                v = v.decode("utf-8", errors="replace")
            if isinstance(v, str):
                out.append(v)
        elif fstring_middle is not None and t.type == fstring_middle:
            out.append(t.string)
    return out


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
        self.mutating = set((iface.get("cad_prefixes") or {}).get("mutating") or [])
        self.bpol = (self.policy.get("backends") or {}).get(self.backend.get("id", ""), {})
        self.scan_patterns = [re.compile(p) for p in self.policy.get("scan_code_patterns") or []]
        gtp = self.policy.get("gt_name_pattern")
        self.gt_pattern = re.compile(gtp) if gtp else None

    # -- state
    def state(self) -> dict:
        """The gate state; ``{"tampered": True, ...}`` when gate_state.json does not match the
        digest the gate keeps outside the run dir (changed, deleted or written by anything but
        the gate)."""
        p = self.run_dir / STATE_FILE
        dp = digest_path(self.run_dir)
        try:
            want = dp.read_text(encoding="utf-8").strip() if dp.is_file() else None
        except OSError:
            want = None
        if not p.is_file():
            if want:
                return {"executed": None, "history": [], "tampered": True,
                        "why": "gate_state.json was deleted"}
            return {"executed": None, "history": []}
        try:
            raw = p.read_bytes()
        except OSError as exc:
            return {"executed": None, "history": [], "tampered": True, "why": str(exc)}
        if want != hashlib.sha256(raw).hexdigest():
            return {"executed": None, "history": [], "tampered": True,
                    "why": "gate_state.json was changed outside the gate"}
        try:
            st = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"executed": None, "history": [], "tampered": True, "why": str(exc)}
        return st if isinstance(st, dict) else {"executed": None, "history": [],
                                                "tampered": True, "why": "not an object"}

    def commit(self, d: Decision) -> None:
        if not (d.allow and d.record):
            return
        st = self.state()
        if st.get("tampered"):
            return
        st["executed"] = d.record
        st.setdefault("history", []).append(d.record)
        p = self.run_dir / STATE_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(st, indent=2) + "\n").encode("utf-8")
        dp = digest_path(self.run_dir)
        dp.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, p)
        dtmp = dp.with_suffix(".tmp")
        dtmp.write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="utf-8")
        os.replace(dtmp, dp)

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
            if tool in (self.policy.get("blocked_tools") or []):
                return Decision(False, f"tool {tool} is not available in benchmark runs",
                                "denied")
            bad = self._foreign_paths(args)
            if bad:
                return Decision(False, f"path outside the run dir: {bad}", "path")
            if tool.startswith("mcp__"):
                why = self._mcp_writes(tool, args)
                if why:
                    return Decision(False, why, "path")
                return self._mcp(tool, args)
            return self._builtin(tool, args)
        except Exception as exc:  # fail closed
            return Decision(False, f"gate error ({type(exc).__name__}: {exc})", "error")

    # paths -------------------------------------------------------------------------------
    def _resolve(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.run_dir / q

    def _foreign_paths(self, args: dict) -> str | None:
        """Path arguments (``gate_policy.yaml: path_args``, any length) and every other short
        single-line string that looks like a path (absolute, ``~``, ``..``) must stay in the run
        dir. Long or multi-line strings that are not path arguments are code / content: code is
        checked by the code-literal rule instead."""
        names = set(self.policy.get("path_args") or [])
        cap = int(self.policy.get("max_path_len") or 1024)

        def walk(v: Any, key: str | None = None):
            if isinstance(v, dict):
                for k, x in v.items():
                    yield from walk(x, k)
            elif isinstance(v, list):
                for x in v:
                    yield from walk(x, key)
            elif isinstance(v, str):
                yield key, v

        for key, s in walk(args):
            short = s[:120] + ("..." if len(s) > 120 else "")
            if key in names and s:
                if len(s) > cap or _CONTROL.search(s):
                    return f"{key}: path argument longer than {cap} characters or with a " \
                           f"control character ({short!r})"
                if s.startswith("~") or not inside(self._resolve(s.split("*")[0] or "."),
                                                   self.run_dir):
                    return short
                continue
            if len(s) > 4096 or "\n" in s or "\r" in s:
                continue  # code / content, not a path
            if self._pathlike(s) and (s.startswith("~") or not inside(
                    self._resolve(s.split("*")[0] or "."), self.run_dir)):
                return short
        return None

    @staticmethod
    def _pathlike(s: str) -> bool:
        return bool(_ABS_PATH.match(s) or s.startswith("~") or ".." in PurePosixPath(
            s.replace("\\", "/")).parts)

    def _write_check(self, tool: str, target: str) -> Decision | None:
        """The write rule for one target path (file tools and MCP write arguments)."""
        p = self._resolve(target)
        if not inside(p, self.run_dir):
            return Decision(False, f"{tool}: {target} is outside the run dir", "path")
        rel = self._rel(p)
        for prot in self.policy.get("protected") or []:
            if rel == prot.rstrip("/") or (prot.endswith("/") and rel.startswith(prot)):
                return Decision(False, f"{rel} is read-only in a benchmark run", "path")
        writable = self.policy.get("writable")
        if writable is not None and not any(rel == w.rstrip("/") or rel.startswith(w)
                                            for w in writable):
            return Decision(False, f"{rel} is read-only in a benchmark run: write only under "
                            + ", ".join(writable), "path")
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
        return None

    def _mcp_writes(self, tool: str, args: dict) -> str | None:
        spec = (self.policy.get("write_args") or {}).get(tool)
        if not spec:
            return None
        for arg, default in spec.items():
            v = args.get(arg)
            if not (isinstance(v, str) and v):
                v = default
                if isinstance(v, str) and "<mesh_dir>" in v:
                    mesh = args.get("mesh_path")
                    base = PurePosixPath(mesh.replace("\\", "/")).parent.as_posix() if \
                        isinstance(mesh, str) and mesh else "."
                    v = v.replace("<mesh_dir>", base)
            if not (isinstance(v, str) and v):
                continue
            d = self._write_check(tool, v)
            if d is not None:
                return f"{tool} {arg}={v!r}: {d.reason}"
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
            d = self._write_check(tool, target)
            if d is not None:
                return d
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
                         source_call=bool(src), mutating=tool in self.mutating)

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
                return f"reads or analyses a mesh / the scan, or runs code the gate cannot " \
                       f"check ({m.group(0)[:60]!r}); scan analysis goes through retk only"
        return self._code_literals(text)

    def _code_literals(self, text: str) -> str | None:
        """String literals naming a path outside the run dir, a protected run-dir path or the
        ground-truth store (best effort: runtime-built paths are not caught)."""
        prot = self.policy.get("protected") or []
        files = {x for x in prot if not x.endswith("/")}
        for s in _string_literals(text):
            if not s or "\n" in s or "\r" in s or len(s) > 4096:
                continue
            if self.gt_pattern is not None and self.gt_pattern.search(s):
                return f"names the ground-truth store ({s[:60]!r})"
            if not s.strip("/\\ \t"):
                continue  # a bare separator ("/".join, " // " in a label)
            if not (self._pathlike(s) or "/" in s or "\\" in s or s in files):
                continue
            if s.startswith("\\") and not s.startswith("\\\\") and not _ROOTED.match(s):
                continue  # r"\d+" and friends
            try:
                p = self._resolve(s)
                ok = not s.startswith("~") and inside(p, self.run_dir)
            except (OSError, ValueError):
                ok = False
            if not ok:
                return f"names a path outside the run dir ({s[:60]!r})"
            rel = self._rel(p)
            for x in prot:
                if rel == x.rstrip("/") or (x.endswith("/") and rel.startswith(x)):
                    return f"names the protected run-dir path {rel!r}"
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
    def _cad(self, tool: str, label: str, source_call: bool, mutating: bool = False) -> Decision:
        ps = plan_status(self.run_dir, self.cfg.get("part_id"))
        if not ps.ok:
            return Decision(False, f"CAD is locked: {ps.reason}", "plan")
        if not (source_call or mutating):
            return Decision(True, "", "cad")
        st = self.state()
        if st.get("tampered"):
            return Decision(False, f"the gate state is not the gate's own ({st.get('why')}): "
                            "CAD execution is closed for this run", "tampered")
        fp = source_fingerprint(self.run_dir, self.bpol.get("source_globs") or ["*.py"])
        ex = st.get("executed")
        if ex and ps.revision <= ex["revision"] and fp != ex["fingerprint"]:
            return Decision(False, f"the CAD source changed after plan revision v{ex['revision']}"
                            f" was executed: write plan/re_plan.v{ex['revision'] + 1}.json "
                            "(a change_log line naming the change) before executing again",
                            "revision")
        if not source_call:
            return Decision(True, "", "cad")
        return Decision(True, "", "cad", {"revision": ps.revision, "fingerprint": fp,
                                          "call": label, "t": round(time.time(), 3)})

    # finish ---------------------------------------------------------------------------------
    def finish_check(self) -> dict:
        """What ``finish`` verifies before it executes the native file in a clean process:
        the gate state is the gate's own, a valid plan exists, the CAD source was executed through
        the gate and has not changed since (build123d: the native script IS the source), and the
        native file plus everything it can import passes the same code checks as a gated
        execution. ``{"ok", "problems": [...], "warnings": [...]}``."""
        problems: list[str] = []
        warnings: list[str] = []
        st = self.state()
        if st.get("tampered"):
            problems.append(f"gate state was not written by the gate ({st.get('why')})")
        ps = plan_status(self.run_dir, self.cfg.get("part_id"))
        if not ps.ok:
            problems.append(f"no valid plan: {ps.reason}")
        executes_source = bool(self.bpol.get("source_tools")) or self.kind == "cli"
        ex = st.get("executed")
        if executes_source and not st.get("tampered"):
            fp = source_fingerprint(self.run_dir, self.bpol.get("source_globs") or ["*.py"])
            if not ex:
                problems.append("the CAD source was never executed through the gate")
            elif fp != ex.get("fingerprint"):
                msg = (f"the CAD source changed after the last gated execution (plan "
                       f"v{ex.get('revision')}): the changed source was never executed")
                if self.kind == "cli":
                    warnings.append(msg + "; the native file is the one that execution saved")
                else:
                    problems.append(msg)
        body = (self.bpol.get("body_as") or {})
        native = self.cfg.get("native_file") or self.bpol.get("native_file")
        if body and native:
            spec = next(iter(body.values()))
            why = self._script_body(native, spec)
            if why:
                problems.append(f"native file check: {why}")
        return {"ok": not problems, "problems": problems, "warnings": warnings}


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
