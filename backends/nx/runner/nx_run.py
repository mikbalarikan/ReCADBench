#!/usr/bin/env python
"""`recadbench nx-run <cad/journal.py>` - the only NX access path for an agent (05_TOOLS.md).

    recadbench nx-run cad/step_01.py [--timeout 120] [--part cad/part.prt]

Contract (05_TOOLS "recadbench nx-run contract"):
  * input: ONE journal path under ``cad/`` of the current run dir; the journal operates on
    ``cad/part.prt`` (created from the plain mm template when absent, else opened);
  * each run: open part -> execute journal -> save -> dump ``cad/last_run.json``
    {ok, error, traceback, features[], bodies, volume, ...} measured by the NX-side wrapper;
  * a run that fails (exception, feature error, timeout) does NOT save: part.prt stays as it was;
  * timeout per call (default 120 s, ``RECADBENCH_NX_TIMEOUT_S``); on timeout only the
    run_journal process started here (and its children) is killed, never NX by name;
  * stdout/stderr go to ``cad/logs/<journal>.log``; an NX syslog excerpt goes into last_run.json;
  * exit 0 if ok, 1 on any NX / journal error, 2 on usage errors. One JSON summary on stdout;
  * before NX starts, __pycache__ folders are purged and the journal plus every other .py in the
    run dir and in lib/ (comments removed) are checked against ``backends/nx/backend.yaml``
    ``denied_tools`` (ADR 0004: journal code that imports or analyses the scan) and the shared
    CAD code policy (``backends/recad_cli/code_policy.py``, profile ``nx``: an AST allow-list, no
    file / process / import-system / introspection access; skillset files compose copied are
    trusted while unchanged); a match, or a compiled / zipped module there, refuses the run
    (exit 1, ``denied: true``, part untouched).

Environment:
  RECADBENCH_NX_RUN_JOURNAL   run_journal.exe (default: %UGII_BASE_DIR%\\NXBIN\\run_journal.exe,
                              then C:\\Program Files\\Siemens\\NX2206\\NXBIN\\run_journal.exe)
  RECADBENCH_NX_TIMEOUT_S     default timeout (s)
  RECADBENCH_NX_LOCK          host-wide lock file serialising NX runs (max_parallel 1);
                              default <tempdir>/recadbench-nx.lock
  RECADBENCH_NX_LIB           folder put on the journal's sys.path
                              (default: <run dir>/lib if present)

Stdlib only (Python >= 3.11). `run_journal()` is also used by backends/nx/adapter/adapter.py.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
WRAPPER = HERE / "nx_wrapper.py"
ADAPTER_DIR = HERE.parent / "adapter"
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_NX = r"C:\Program Files\Siemens\NX2206"
# Variables of session-capture / customisation plugins that must not leak into a batch run,
# and the gate's key-store location (the journal is agent code: runner/recadbench_run/gate_key.py).
STRIP_ENV = ("UGII_CUSTOM_DIRECTORY_FILE", "UGII_STARTUP_MACRO", "RECADBENCH_GATE_KEY_DIR")
CODE_POLICY = HERE.parent.parent / "recad_cli" / "code_policy.py"
SYSLOG_KEYS = ("error", "exception", "fail", "traceback", "fatal", "warning: ")


class NxEnvError(RuntimeError):
    """NX is not usable on this host."""


def find_run_journal() -> Path:
    cands = []
    if os.environ.get("RECADBENCH_NX_RUN_JOURNAL"):
        cands.append(Path(os.environ["RECADBENCH_NX_RUN_JOURNAL"]))
    base = os.environ.get("UGII_BASE_DIR")
    if base:
        cands.append(Path(base) / "NXBIN" / "run_journal.exe")
    cands.append(Path(DEFAULT_NX) / "NXBIN" / "run_journal.exe")
    for c in cands:
        if c.is_file():
            return c
    raise NxEnvError(
        "run_journal.exe not found (set RECADBENCH_NX_RUN_JOURNAL or UGII_BASE_DIR); "
        f"tried {[str(c) for c in cands]}"
    )


@contextlib.contextmanager
def nx_lock(timeout: float):
    """Host-wide exclusive lock: one NX batch process at a time (backend.yaml max_parallel 1)."""
    path = Path(
        os.environ.get("RECADBENCH_NX_LOCK") or Path(tempfile.gettempdir()) / "recadbench-nx.lock"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: SIM115 - held for the whole run
    t0 = time.time()
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() - t0 > timeout:
                    raise NxEnvError(f"NX lock {path} still held after {timeout:g} s") from None
                time.sleep(0.5)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the run_journal process started here and its children (never NX by name)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()


def run_journal(
    script: Path,
    args: list[str],
    work_dir: Path,
    timeout: float,
    env_extra: dict[str, str] | None = None,
    lock_timeout: float = 1800.0,
) -> dict[str, Any]:
    """Run one journal headless; {exit_code, timed_out, elapsed_s, stdout, stderr}.

    The run_journal exit code is NOT a success signal (NX 2206 returns 1 after correct journals and
    0 after failed ones): callers read the result file the journal wrote."""
    exe = find_run_journal()
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update(env_extra or {})
    cmd = [str(exe), str(script)]
    if args:
        cmd += ["-args", *[str(a) for a in args]]  # NX 2206 run_journal rejects -nx: never pass it
    work_dir.mkdir(parents=True, exist_ok=True)
    with nx_lock(lock_timeout):
        t0 = time.time()
        proc = subprocess.Popen(
            cmd,
            cwd=str(work_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            out, err = proc.communicate()
        return {
            "exit_code": proc.returncode,
            "timed_out": timed_out,
            "elapsed_s": round(time.time() - t0, 3),
            "stdout": out.decode("utf-8", "replace"),
            "stderr": err.decode("utf-8", "replace"),
            "started": t0,
        }


def syslog_excerpt(
    path: str | None, started: float, max_lines: int = 40, offset: int = 0
) -> list[str]:
    """Error-ish lines of the NX syslog of this run (the wrapper reports its path; when it died
    before that, the newest *.syslog in the temp folder written after the start)."""
    p = Path(path) if path else None
    if p is None or not p.is_file():
        offset = 0
        tmp = Path(os.environ.get("TEMP") or tempfile.gettempdir())
        logs = [q for q in tmp.glob("*.syslog") if q.stat().st_mtime >= started - 1]
        p = max(logs, key=lambda q: q.stat().st_mtime) if logs else None
    if p is None:
        return []
    try:
        with open(p, "rb") as fh:
            fh.seek(offset)
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    hits = [ln.strip() for ln in lines if any(k in ln.lower() for k in SYSLOG_KEYS)]
    hits = [h for h in hits if h and "error handler" not in h.lower()]
    return [h.encode("ascii", "replace").decode("ascii")[:300] for h in hits[-max_lines:]]


def run_wrapped(
    journal: Path,
    part: Path,
    result: Path,
    timeout: float,
    lib_dir: Path | None = None,
    log: Path | None = None,
) -> dict[str, Any]:
    """Execute a journal through nx_wrapper.py on `part`; returns the last_run dict."""
    if result.exists():
        result.unlink()
    args = [str(journal), str(part), str(result), str(ADAPTER_DIR)]
    if lib_dir is not None:
        args.append(str(lib_dir))
    # no bytecode caches next to checked sources (nx-run also purges them before each run)
    env_extra = {"RECADBENCH_NX_PART": str(part), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        run = run_journal(WRAPPER, args, part.parent, timeout, env_extra)
    except NxEnvError as exc:
        return {
            "ok": False,
            "error": f"environment: {exc}",
            "journal": str(journal),
            "part": str(part),
        }
    res: dict[str, Any]
    try:
        res = json.loads(result.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        res = {
            "ok": False,
            "journal": str(journal),
            "part": str(part),
            "error": (
                f"timeout after {timeout:g} s (run killed, part not saved)"
                if run["timed_out"]
                else "NX wrote no result (crash or syntax error in the wrapper); see log"
            ),
        }
    if run["timed_out"]:
        res.update(ok=False, error=f"timeout after {timeout:g} s (run killed, part not saved)")
    res["timed_out"] = run["timed_out"]
    res["exit_code"] = run["exit_code"]
    res["elapsed_s"] = run["elapsed_s"]
    res["syslog_excerpt"] = syslog_excerpt(
        res.get("syslog"), run["started"], offset=int(res.get("syslog_offset") or 0)
    )
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            f"$ run_journal {journal}\n--- stdout ---\n{run['stdout']}\n"
            f"--- stderr ---\n{run['stderr']}\nexit {run['exit_code']}\n",
            encoding="utf-8",
            newline="\n",
        )
        res["log"] = str(log)
    # Syntax errors of the journal itself only appear in run_journal's output.
    if not res.get("ok") and not res.get("traceback") and "Syntax" in run["stdout"]:
        res["traceback"] = run["stdout"][-3000:]
    return res


BACKEND_YAML = HERE.parent / "backend.yaml"
COMMAND = "recadbench nx-run"


def _yaml_scalar(text: str) -> str:
    """A plain or single-quoted YAML scalar on one line (the form backend.yaml uses)."""
    text = text.strip()
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    if text.startswith('"'):
        return json.loads(text)
    return text


def denied_rules(path: Path = BACKEND_YAML) -> list[dict[str, str]]:
    """backend.yaml `denied_tools` (stdlib only: a reader for the flat list form of that block;
    backends/nx/tests checks it against yaml.safe_load). Continuation lines of a plain `reason`
    are joined with a space."""
    rules: list[dict[str, str]] = []
    inside = False
    last = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("denied_tools:"):
            inside = True
            continue
        if not inside or raw.lstrip().startswith("#") or not raw.strip():
            continue
        if not raw.startswith((" ", "-")):
            break  # next top-level key
        line = raw[2:] if raw.startswith("- ") else raw
        if raw.startswith("- "):
            rules.append({})
        key, sep, val = line.strip().partition(": ")
        if sep and key in ("tool", "args_regex", "reason") and line.startswith(("  ", key)):
            rules[-1][key] = _yaml_scalar(val)
            last = key
        elif last == "reason":
            rules[-1]["reason"] += " " + raw.strip()
    return rules


def denied(command: str, journal_text: str, rules: list[dict[str, str]] | None = None):
    """The first denied_tools rule that matches this run (ADR 0004 matcher: `tool` is a prefix of
    the command, `args_regex` is searched in json.dumps(arguments)), or None. The journal is
    matched without its # comments (strip_comments; pass journal_source(bytes) for a file)."""
    import re

    rules = denied_rules() if rules is None else rules
    blob = json.dumps(
        {"command": command, "journal": strip_comments(journal_text)}, ensure_ascii=False
    )
    for r in rules:
        if command.startswith(r["tool"]) and (
            "args_regex" not in r or re.search(r["args_regex"], blob)
        ):
            return r
    return None


def strip_comments(text: str) -> str:
    """Python source with its `#` comments removed (the text the deny matcher sees).

    Line ends are normalised first (CPython also ends a line at a lone CR, so a comment cannot
    hide code behind one). Strings and docstrings are kept: a docstring is reachable at run
    time (`exec(__doc__)`), a comment is not. Source that does not tokenize is returned
    unchanged, i.e. matched with its comments (strict)."""
    import io
    import tokenize

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cut: dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                cut[tok.start[0]] = tok.start[1]
    except (tokenize.TokenError, SyntaxError):
        return text
    lines = text.split("\n")
    for row, col in cut.items():
        lines[row - 1] = lines[row - 1][:col]
    return "\n".join(lines)


def journal_source(raw: bytes) -> str:
    """The `journal` argument of the deny matcher for one Python file: the text Python decodes
    from these bytes (PEP 263 coding cookie honoured, so a `coding: utf-7` file is matched as
    Python reads it), with `#` comments removed."""
    import io
    import tokenize

    try:
        enc, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        text = raw.decode(enc, errors="replace")
    except (SyntaxError, LookupError):
        text = raw.decode("utf-8", errors="replace")
    return strip_comments(text)


# Importable code the deny regex cannot read (sourceless bytecode, extension modules, zip
# imports). Refused anywhere in the run dir / lib (__pycache__ is purged before every run).
OPAQUE_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".zip", ".egg", ".whl", ".pyz")
OPAQUE_REASON = "compiled or packaged Python module (not checkable against denied_tools)"


def lib_dir(run_dir: Path) -> Path | None:
    """Folder put on the journal's sys.path: RECADBENCH_NX_LIB, else <run dir>/lib."""
    if os.environ.get("RECADBENCH_NX_LIB"):
        return Path(os.environ["RECADBENCH_NX_LIB"])
    return run_dir / "lib" if (run_dir / "lib").is_dir() else None


def _code_roots(run_dir: Path) -> list[Path]:
    roots = [run_dir]
    lib = lib_dir(run_dir)
    if lib is not None and lib.is_dir() and not lib.resolve().is_relative_to(run_dir):
        roots.append(lib.resolve())
    return roots


def purge_bytecode(run_dir: Path) -> None:
    """Remove __pycache__ folders under the run dir and lib/: a planted cache .pyc (with the
    mtime/size header of an innocent source) would run instead of the checked source."""
    import shutil

    for root in _code_roots(run_dir):
        for d in sorted(root.rglob("__pycache__"), reverse=True):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def code_policy():
    """backends/recad_cli/code_policy.py (stdlib only), loaded by path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("recadbench_code_policy", CODE_POLICY)
    if spec is None or spec.loader is None or not CODE_POLICY.is_file():
        raise NxEnvError(f"code policy missing at {CODE_POLICY}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_journal(run_dir: Path, journal: Path, command: str) -> str | None:
    """Reason text when code the journal can import is denied by backend.yaml denied_tools or
    by the CAD code policy; None when the run may start.

    Checked: the journal, then every .py anywhere in the run dir (cad/ siblings, lib/, the run
    root, any folder a journal can put on sys.path by a literal path) and in RECADBENCH_NX_LIB
    when that lies outside the run dir; any importable file the regex cannot read
    (OPAQUE_SUFFIXES) refuses the run too. The code policy denies the means to reach code
    through runtime-built paths (os, sys.path, zipfile, importlib, open, exec, ...); what the
    NX Open API itself can do stays a residual risk (runner README "Security model")."""
    rules = denied_rules()
    cp = code_policy()
    pins = cp.pinned(run_dir)
    files = [journal]
    for root in _code_roots(run_dir):
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() in OPAQUE_SUFFIXES and "__pycache__" not in p.parts:
                return f"denied: {_rel(p, run_dir)}: {OPAQUE_REASON}"
            # .pyw is a source suffix of the import system on Windows
            if p.suffix.lower() in (".py", ".pyw") and p.resolve() != journal and p.is_file():
                files.append(p)
    texts = []
    for f in files:
        try:
            text = journal_source(f.read_bytes())
        except OSError:
            continue
        hit = denied(command, text, rules)
        if hit:
            return f"denied: {_rel(f, run_dir)}: {hit.get('reason', 'backend.yaml denied_tools')}"
        texts.append((f, text))
    # then the CAD code policy over the same files (a scan reader is reported as such first)
    for f, text in texts:
        rel = _rel(f, run_dir)
        if rel.startswith("lib/") and not cp.is_pinned(run_dir, f, pins):
            return f"denied: {rel}: lib/ differs from what compose copied"
        why = cp.check_file(run_dir, f, "nx", text, pins)
        if why:
            return f"denied: {why}"
    return None


def _rel(p: Path, run_dir: Path) -> str:
    p = p.resolve()
    return p.relative_to(run_dir).as_posix() if p.is_relative_to(run_dir) else p.as_posix()


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recadbench nx-run", description=__doc__.split("\n\n")[0])
    ap.add_argument("journal", help="NX Open Python journal under cad/")
    ap.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("RECADBENCH_NX_TIMEOUT_S") or DEFAULT_TIMEOUT_S),
    )
    ap.add_argument(
        "--part",
        default="cad/part.prt",
        help="part file the journal works on (must be under cad/; default cad/part.prt)",
    )
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2

    run_dir = Path.cwd().resolve()
    cad = run_dir / "cad"
    journal = Path(a.journal)
    journal = (journal if journal.is_absolute() else run_dir / journal).resolve()
    part = Path(a.part)
    part = (part if part.is_absolute() else run_dir / part).resolve()

    def usage(msg: str) -> int:
        print(json.dumps({"ok": False, "error": f"usage: {msg}"}))
        return 2

    if not journal.is_relative_to(cad) or journal.suffix != ".py":
        return usage(f"journal must be a .py file under {cad} (got {a.journal})")
    if not journal.is_file():
        return usage(f"no journal {journal}")
    if not part.is_relative_to(cad) or part.suffix.lower() != ".prt":
        return usage(f"part must be a .prt under {cad} (got {a.part})")
    purge_bytecode(run_dir)
    deny = check_journal(run_dir, journal, f"{COMMAND} {a.journal}")
    if deny:
        res = {
            "ok": False,
            "error": deny,
            "denied": True,
            "saved": False,
            "journal": journal.relative_to(run_dir).as_posix(),
            "part": part.relative_to(run_dir).as_posix(),
        }
        _write_json(cad / "last_run.json", res)
        print(
            json.dumps(
                {"ok": False, "error": deny, "denied": True, "last_run": "cad/last_run.json"}
            )
        )
        return 1
    lib = lib_dir(run_dir)

    res = run_wrapped(
        journal,
        part,
        cad / ".last_run.tmp.json",
        a.timeout,
        lib,
        cad / "logs" / f"{journal.stem}.log",
    )
    with contextlib.suppress(OSError):
        (cad / ".last_run.tmp.json").unlink()
    res["journal"] = journal.relative_to(run_dir).as_posix()
    res["part"] = part.relative_to(run_dir).as_posix()
    if res.get("log"):
        res["log"] = Path(res["log"]).relative_to(run_dir).as_posix()
    res.pop("syslog", None)
    res.pop("syslog_offset", None)
    _write_json(cad / "last_run.json", res)
    summary = {
        k: res.get(k)
        for k in ("ok", "error", "saved", "count", "bodies", "volume", "unmapped", "elapsed_s")
    }
    summary["features"] = [f["taxonomy_type"] for f in res.get("features") or [] if f["counted"]]
    summary["last_run"] = "cad/last_run.json"
    print(json.dumps(summary))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
