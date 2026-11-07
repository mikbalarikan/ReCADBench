#!/usr/bin/env python
"""recad nx adapter: measurement instrument for the NX backend (L1, 00_DECISIONS D3).

Run with any Python >= 3.11 on a host with NX 2206 (stdlib only; never imports recadbench_spec):

    python adapter.py export    <native.prt> --out <file.step>
    python adapter.py extract   <native.prt> --out <features.json>
    python adapter.py set-param <native.prt> <name> <value> --out <file.step> [--native-out <f>]
    python adapter.py replay    <case_id> --out-dir <dir>
    python adapter.py version

Each subcommand prints exactly one JSON object on stdout and exits 0 on success, 1 on failure.
The NX work happens in `nx_adapter_journal.py` / `../runner/nx_wrapper.py`, run headless through
run_journal.exe by `../runner/nx_run.py` (one NX process at a time per host). The input .prt is
never written: set-param works on a copy.

Native model: one .prt holding one solid body; driving dimensions are user-named NX expressions
(IR `parameters[].name`). `extract` derives the feature list from the NX feature tree
(nx_taxonomy.py), never from agent self-report. Replay = the conformance journal run through the
very same wrapper an agent's `recadbench nx-run` uses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPLAYS = HERE.parent / "replays"
JOURNAL = HERE / "nx_adapter_journal.py"
sys.path.insert(0, str(HERE.parent / "runner"))

import nx_run  # noqa: E402

TIMEOUT_S = float(os.environ.get("RECAD_NX_JOURNAL_TIMEOUT_S") or 600)


def emit(obj: dict[str, Any]) -> int:
    print(json.dumps(obj))
    sys.stdout.flush()
    return 0 if obj.get("ok") else 1


def log(msg: str) -> None:
    print(f"[recad nx] {msg}", file=sys.stderr)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8", newline="\n")


def nx_call(req: dict[str, Any], work: Path) -> dict[str, Any]:
    """Run nx_adapter_journal.py once; its result dict (ok False + error on any failure)."""
    req = {**req, "adapter_dir": str(HERE)}
    rq, rs = work / "request.json", work / "result.json"
    _write_json(rq, req)
    run = nx_run.run_journal(JOURNAL, [str(rq), str(rs)], work, TIMEOUT_S)
    try:
        res = json.loads(rs.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tail = (run["stdout"] + run["stderr"])[-1500:]
        res = {
            "ok": False,
            "error": ("NX timed out" if run["timed_out"] else "NX journal wrote no result")
            + f": {tail}",
        }
    if res.get("traceback"):
        log(res["traceback"])
    return res


_READBACK = (
    "import json, sys\n"
    "from build123d import import_step, __version__\n"
    "s = import_step(sys.argv[1])\n"
    "print('RECAD_JSON ' + json.dumps({'solids': len(s.solids()), 'volume_mm3': "
    "round(float(s.volume), 6), 'faces': len(s.faces()), 'by': 'build123d ' + __version__}))\n"
)


READBACK_REL_TOL = 1e-3  # relative volume, NX vs the independent STEP read-back


def _readback(step: Path) -> dict[str, Any] | None:
    """Independent STEP read-back (solids, volume, faces) with build123d, in a child process:
    OCCT prints reader messages straight to fd 1 (also at exit), which must stay off our stdout.
    Uses this interpreter if it has build123d, else RECAD_BUILD123D_PYTHON; None without one."""
    import importlib.util

    py = (
        sys.executable
        if importlib.util.find_spec("build123d")
        else (os.environ.get("RECAD_BUILD123D_PYTHON") or None)
    )
    if not py:
        return None
    proc = subprocess.run(
        [py, "-c", _READBACK, str(step)], capture_output=True, text=True, timeout=600
    )
    for ln in proc.stdout.splitlines():
        if ln.startswith("RECAD_JSON "):
            return json.loads(ln[len("RECAD_JSON ") :])
    log(f"STEP read-back failed: {proc.stderr[-800:]}")
    return {"solids": 0, "volume_mm3": 0.0, "faces": 0, "by": "read-back failed"}


def _step_result(res: dict[str, Any], step: Path) -> dict[str, Any]:
    out = {
        "ok": bool(res.get("ok")),
        "solids": res.get("bodies", 0),
        "volume_mm3": res.get("volume", 0.0),
        "faces": res.get("faces", 0),
    }
    if res.get("error"):
        out["error"] = res["error"]
    if not out["ok"]:
        return out
    out.update(step=str(step), unit=res.get("unit"))
    rb = _readback(step)
    if rb is None:
        out["needs_measure"] = True  # the dispatcher measures it (RECAD_BUILD123D_PYTHON)
        return out
    out["readback"] = rb
    v = out["volume_mm3"]
    # NX (Parasolid) and the read-back (OCCT) integrate curved faces differently: P11 saw 3e-5 ..
    # 1e-4 relative on a spherical dome, so 1e-5 rejected valid parts. Same cross-kernel
    # tolerance as the Solidcraft adapter; a wrong or broken export is off by far more.
    if rb["solids"] != 1 or abs(rb["volume_mm3"] - v) > READBACK_REL_TOL * max(1.0, abs(v)):
        out.update(ok=False, error=f"STEP read-back mismatch: {rb} vs NX volume {v}")
    return out


def _features_doc(res: dict[str, Any], native: Path) -> dict[str, Any]:
    doc = dict(res["features"])
    doc["native_file"] = str(native.resolve())
    doc["native_sha256"] = _sha256(native)
    return doc


# ---------------------------------------------------------------------------- subcommands
def cmd_export(a, work: Path) -> int:
    out = Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    res = nx_call(
        {"cmd": "inspect", "native": str(Path(a.native).resolve()), "step": str(out)}, work
    )
    return emit(_step_result(res, out))


def cmd_extract(a, work: Path) -> int:
    native = Path(a.native).resolve()
    res = nx_call({"cmd": "inspect", "native": str(native), "step": None}, work)
    if "features" not in res:
        return emit({"ok": False, "error": res.get("error", "extract failed")})
    doc = _features_doc(res, native)
    _write_json(Path(a.out).resolve(), doc)
    out = {
        "ok": bool(res.get("ok")),
        "count": doc["count"],
        "unmapped": len(doc["unmapped"]),
        "features": [f["taxonomy_type"] for f in doc["features"] if f["counted"]],
        "out": str(Path(a.out).resolve()),
    }
    if res.get("error"):
        out["error"] = res["error"]
    return emit(out)


def cmd_set_param(a, work: Path) -> int:
    src = Path(a.native).resolve()
    copy = work / "set_param_input.prt"  # the input .prt is never opened for writing
    shutil.copyfile(src, copy)
    native_out = work / "set_param_output.prt"
    out = Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    res = nx_call(
        {
            "cmd": "set-param",
            "native": str(copy),
            "name": a.name,
            "value": a.value,
            "native_out": str(native_out),
            "step": str(out),
        },
        work,
    )
    step = _step_result(res, out)
    r = {
        "ok": step["ok"],
        "parameter": a.name,
        "value": a.value,
        "volume_before_mm3": res.get("volume_before_mm3"),
        "volume_after_mm3": step["volume_mm3"] if step["ok"] else None,
        "old_formula": res.get("old_formula"),
    }
    for k in ("error", "needs_measure", "readback"):
        if k in step:
            r[k] = step[k]
    if step["ok"] and a.native_out and native_out.is_file():
        dst = Path(a.native_out).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(native_out, dst)
        r["native_out"] = str(dst)
    return emit(r)


def cmd_replay(a, work: Path) -> int:
    script = REPLAYS / f"{a.case_id}.py"
    if not script.is_file():
        return emit({"ok": False, "error": f"no replay {script}"})
    out_dir = Path(a.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    native = out_dir / "native.prt"
    if native.exists():
        native.unlink()
    run = nx_run.run_wrapped(
        script, native, work / "last_run.json", TIMEOUT_S, log=out_dir / "replay.log"
    )
    _write_json(out_dir / "last_run.json", run)
    if not run.get("ok") or not native.is_file():
        return emit({"ok": False, "error": f"replay journal failed: {run.get('error')}"})
    # export + extract read the SAVED native file back (not the in-memory build)
    step = out_dir / "output.step"
    res = nx_call({"cmd": "inspect", "native": str(native), "step": str(step)}, work)
    if "features" not in res:
        return emit({"ok": False, "error": res.get("error", "inspect failed")})
    feats = _features_doc(res, native)
    _write_json(out_dir / "features.json", feats)
    sr = _step_result(res, step)
    ok = sr["ok"] and not feats["unmapped"]
    r = {
        "ok": ok,
        "volume_mm3": sr["volume_mm3"],
        "faces": sr["faces"],
        "count": feats["count"],
        "solids": sr["solids"],
        "out_dir": str(out_dir),
    }
    if "needs_measure" in sr:
        r["needs_measure"] = True
    if not ok:
        r["error"] = sr.get("error") or f"unmapped: {feats['unmapped']}"
    return emit(r)


def cmd_version(a, work: Path) -> int:
    try:
        exe = nx_run.find_run_journal()
    except nx_run.NxEnvError as e:
        return emit({"ok": False, "error": str(e)})
    nx_dir = exe.parent.parent.name  # e.g. NX2206
    r = {"ok": True, "version": f"Siemens {nx_dir} (run_journal {exe})"}
    if not os.environ.get("SPLM_LICENSE_SERVER"):
        r["warning"] = "SPLM_LICENSE_SERVER is not set in this environment"
    return emit(r)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Usage errors become a JSON failure (exit 1) instead of argparse's exit 2."""

    def error(self, message: str):  # noqa: D401 - argparse hook
        self.print_usage(sys.stderr)
        raise _UsageError(f"{self.prog}: {message}")


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(prog="recad nx", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export", help="canonical STEP export of the single solid body")
    p.add_argument("native")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("extract", help="feature tree -> features.json (taxonomy)")
    p.add_argument("native")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("set-param", help="set a user expression on a copy, update, export")
    p.add_argument("native")
    p.add_argument("name")
    p.add_argument("value", type=float)
    p.add_argument("--out", required=True)
    p.add_argument("--native-out")
    p.set_defaults(fn=cmd_set_param)
    p = sub.add_parser("replay", help="run replays/<case_id>.py via nx-run, then export + extract")
    p.add_argument("case_id")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(fn=cmd_replay)
    p = sub.add_parser("version", help="locate run_journal.exe (no NX start)")
    p.set_defaults(fn=cmd_version)
    try:
        a = ap.parse_args(argv)
    except _UsageError as e:
        return emit({"ok": False, "error": f"usage: {e}"})
    for attr in ("native",):
        if hasattr(a, attr) and not Path(getattr(a, attr)).is_file():
            return emit({"ok": False, "error": f"no native file {getattr(a, attr)}"})
    work = Path(tempfile.mkdtemp(prefix="recad-nx-"))
    try:
        return a.fn(a, work)
    except Exception as e:  # noqa: BLE001 - the contract is: one JSON object, exit 1
        traceback.print_exc(file=sys.stderr)
        return emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
    finally:
        if not os.environ.get("RECAD_NX_KEEP_WORK"):
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
