"""``recadbench finish``: post-run extraction on the worker, hashes, enqueue (00_DECISIONS D7).

    recadbench finish --run-dir runs/<id> [--score] [--queue sqlite:///q.db | http://host:port]

Runs after the agent session, in a clean process, and leaves the run-dir contract of
``evaluator/recadbench_eval/README.md``:

| Path | Made by finish |
|---|---|
| ``out/features.json`` | ``recad <b> extract <native>`` (an agent-written copy is dropped) |
| ``out/output.step`` | ``recad <b> export <native>`` (scanner frame, mm) |
| ``plan/re_plan.v<N>.json`` | kept as the agent wrote them (hashed) |
| ``efficiency.json`` | the agent backend's counters (written here only if missing) |
| ``run_manifest.json`` | + ``native_file``, ``native_sha256``, ``hashes``, ``finish`` |

The native file is agent-written code (build123d: ``cad/part.py``), so finish executes it only
after :meth:`Gate.finish_check` passed (gate state is the gate's own, valid plan, the source was
executed through the gate and is unchanged since, the native file and its importables pass the
gate's code checks), and then only:

* on a COPY of ``cad/`` and ``lib/`` in a temporary folder outside the run dir (the native code
  cannot reach ``out/``, the manifests or the plan);
* in ``python -I`` (no cwd / PYTHONPATH / user-site on ``sys.path``: a planted ``recad_cli``
  package in the run dir is never imported) with cwd = that temporary folder;
* with the agent environment minus every secret (``RECADBENCH_GT_DIR``, tokens) and
  ``RECAD_SANDBOX=1`` (the build123d adapter then runs the script with restricted builtins and an
  import allow-list, like build123d-mcp's sandbox);
* export first, extract last, each into its own fresh folder; both results are read into memory
  and written to ``out/`` only after the last native execution.

A failed check is recorded as ``finish.error`` and nothing is extracted (the evaluator then fails
the gate: no ``out/output.step``). Then optionally ``recadbench-eval score`` (only where
``RECADBENCH_GT_DIR`` exists: the evaluator host) and/or enqueue a ``score`` job for the evaluator
host. Track S (plan only) skips extraction.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .artifacts import hash_tree, sha256_file
from .gate_hook import GATE_FILE, PLAN_RE, Gate

AGENT_OUTPUTS = ("out/features.json", "features.json", "out/output.step")
UNSAFE_ENV = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONINSPECT",
              "RECADBENCH_RUN_DIR")


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else None


def clean_env() -> dict[str, str]:
    """Environment of the processes that execute agent code: no secrets, no Python path
    overrides, the adapter sandbox on."""
    from .compose import SECRET_ENV

    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV and k not in UNSAFE_ENV}
    env["RECAD_SANDBOX"] = "1"
    return env


def recad(backend: str, *args: str, cwd: Path, timeout: float = 1800.0,
          env: dict[str, str] | None = None) -> dict:
    """One ``recad <backend> ...`` call in a fresh, isolated process; its single JSON result."""
    cmd = [sys.executable, "-I", "-m", "recad_cli", backend, *args]
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=env if env is not None else clean_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"recad {backend} {args[0]} timed out after {timeout} s"}
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    try:
        res = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        res = {}
    if not isinstance(res, dict):
        res = {}
    res.setdefault("ok", p.returncode == 0)
    if p.returncode != 0:
        res["ok"] = False
        res.setdefault("error", (p.stderr or p.stdout).strip()[-2000:] or f"exit {p.returncode}")
    res["exit_code"] = p.returncode
    return res


def plan_revisions(run_dir: Path) -> list[str]:
    d = run_dir / "plan"
    if not d.is_dir():
        return []
    found = sorted((int(m.group(1)), p.name) for p in d.iterdir() if (m := PLAN_RE.match(p.name)))
    return [f"plan/{n}" for _, n in found]


def _copy_sources(run: Path, work: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "logs")
    for d in ("cad", "lib"):
        if (run / d).is_dir():
            shutil.copytree(run / d, work / d, ignore=ignore, symlinks=False)


def extract_isolated(run: Path, backend: str, native_rel: str, timeout: float) -> dict:
    """export + extract of a copy of the native file; returns the report and the two results'
    bytes (``None`` when a step failed)."""
    native = run / native_rel
    before = sha256_file(native)
    rep: dict = {}
    with tempfile.TemporaryDirectory(prefix="recad-finish-") as td:
        work = Path(td)
        _copy_sources(run, work)
        copy = work / native_rel
        env = clean_env()
        exp = work / "o_export"
        exp.mkdir()
        st = recad(backend, "export", str(copy), "--out", str(exp / "output.step"), cwd=work,
                   timeout=timeout, env=env)
        rep["export"] = {k: st.get(k) for k in ("ok", "error", "solids", "volume_mm3", "faces",
                                                "exit_code") if k in st}
        step = (exp / "output.step").read_bytes() if st.get("ok") and \
            (exp / "output.step").is_file() else None
        ext = work / "o_extract"
        ext.mkdir()
        ex = recad(backend, "extract", str(copy), "--out", str(ext / "features.json"), cwd=work,
                   timeout=timeout, env=env)
        rep["extract"] = {k: ex.get(k) for k in ("ok", "error", "count", "unmapped",
                                                 "exit_code") if k in ex}
        feats = (ext / "features.json").read_bytes() if (ext / "features.json").is_file() \
            else None
        copy_after = sha256_file(copy) if copy.is_file() else None
    if sha256_file(native) != before or copy_after != before:
        rep["error"] = "the native file changed while finish executed it"
        return {**rep, "_step": None, "_features": None}
    if feats is not None and native.suffix == ".py":
        try:
            doc = json.loads(feats.decode("utf-8"))
            want = hashlib.sha256(native.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if doc.get("native_sha256") not in (None, want):
                rep["error"] = "features.json does not describe the native file"
                feats = None
        except (UnicodeDecodeError, json.JSONDecodeError):
            feats = None
            rep["error"] = "features.json is not JSON"
    return {**rep, "_step": step, "_features": feats}


def finish(run_dir: str | Path, *, score: bool = False, enqueue=None,
           timeout: float = 1800.0, efficiency: dict | None = None) -> dict:
    run = Path(run_dir).resolve()
    manifest = _json(run / "run_manifest.json")
    if manifest is None:
        raise FileNotFoundError(f"{run}/run_manifest.json missing (not a composed run dir)")
    gate_cfg = _json(run / GATE_FILE) or {}
    backend = manifest["backend"]["id"]
    report: dict = {"backend": backend, "t_start": round(time.time(), 3)}
    if efficiency is not None:
        (run / "efficiency.json").write_text(json.dumps(efficiency, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")
    elif not (run / "efficiency.json").is_file():
        (run / "efficiency.json").write_text(json.dumps({"turns": 0}) + "\n",
                                             encoding="utf-8", newline="\n")
    for rel in AGENT_OUTPUTS:  # never self-report: the adapter makes these
        p = run / rel
        if p.is_file():
            report.setdefault("discarded", []).append(rel)
            p.unlink()
    (run / "out").mkdir(exist_ok=True)
    native_rel = gate_cfg.get("native_file")
    native = run / native_rel if native_rel else None
    if backend == "none":
        report["skipped"] = "plan-only run (Track S)"
    elif native is None or not native.is_file():
        report["error"] = f"native file {native_rel} missing: nothing to extract"
    else:
        chk = Gate(run).finish_check()
        if chk["warnings"]:
            report["warnings"] = chk["warnings"]
        if not chk["ok"]:
            report["error"] = "not extracted: " + "; ".join(chk["problems"])
        else:
            res = extract_isolated(run, backend, native_rel, timeout)
            for k in ("export", "extract", "error"):
                if k in res:
                    report[k] = res[k]
            if res["_step"] is not None:
                (run / "out" / "output.step").write_bytes(res["_step"])
            if res["_features"] is not None:
                (run / "out" / "features.json").write_bytes(res["_features"])
    report["t_end"] = round(time.time(), 3)

    rels = ["input/input.stl", "input/task.yaml", "efficiency.json", *plan_revisions(run),
            "out/output.step", "out/features.json"]
    cad = run / "cad"
    if cad.is_dir():
        rels += [p.relative_to(run).as_posix() for p in sorted(cad.rglob("*"))
                 if p.is_file() and "__pycache__" not in p.parts and "logs" not in p.parts]
    hashes = hash_tree(run, sorted(set(rels)))
    manifest["hashes"] = hashes
    if native is not None and native.is_file():
        manifest["native_file"] = native_rel
        manifest["native_sha256"] = sha256_file(native)
    manifest["finish"] = report
    from recadbench_spec import validate_document

    rep = validate_document(manifest, "run_manifest")
    if not rep.ok:
        raise ValueError("run_manifest invalid after finish: "
                         + "; ".join(str(e) for e in rep.errors[:3]))
    (run / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                           encoding="utf-8", newline="\n")
    exported = (run / "out" / "output.step").is_file()
    out = {"ok": backend == "none" or (exported and "error" not in report
                                       and bool((report.get("export") or {}).get("ok"))),
           "run_dir": str(run), "finish": report}
    if score:
        out["score"] = run_score(run, manifest["part_id"])
    if enqueue is not None:
        out["enqueued"] = enqueue({"kind": "score", "run_id": manifest["run_id"],
                                   "part_id": manifest["part_id"], "run_dir": str(run),
                                   "native_sha256": manifest.get("native_sha256")})
    return out


def run_score(run: Path, part_id: str, timeout: float = 3600.0) -> dict:
    """``recadbench-eval score`` in its own isolated process -> <run>/result.json."""
    if not os.environ.get("RECADBENCH_GT_DIR"):
        return {"ok": False, "skipped": "RECADBENCH_GT_DIR not set (score on the evaluator host)"}
    cmd = [sys.executable, "-I", "-m", "recadbench_eval.cli", "score", "--run-dir",
           str(Path(run).resolve()), "--part", part_id]
    env = {k: v for k, v in os.environ.items() if k not in UNSAFE_ENV}
    with tempfile.TemporaryDirectory(prefix="recad-score-") as td:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=td, env=env)
    res = {"ok": p.returncode == 0, "result": str(run / "result.json")}
    if p.returncode != 0:
        res["error"] = (p.stderr or p.stdout).strip()[-2000:]
    return res


def main_finish(a) -> int:
    enq = None
    if a.queue:
        from .queue_client import enqueue_score

        def enq(job):
            return enqueue_score(a.queue, job)
    try:
        res = finish(a.run_dir, score=a.score, enqueue=enq, timeout=a.timeout)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(res))
    return 0 if res["ok"] else 1
