"""``recadbench finish``: post-run extraction on the worker, hashes, enqueue (00_DECISIONS D7).

    recadbench finish --run-dir runs/<id> [--score] [--queue sqlite:///q.db | http://host:port]

Runs after the agent session, in a clean process, and leaves the run-dir contract of
``evaluator/recadbench_eval/README.md``:

| Path | Made by finish |
|---|---|
| ``out/features.json`` | ``recad <b> extract <native>`` (an agent-written copy is dropped) |
| ``out/output.step`` | ``recad <b> export <native>`` (scanner frame, mm) |
| ``plan/re_plan.v<N>.json`` | kept as the agent wrote them (hashed) |
| ``efficiency.json`` | the agent backend's counters (written here if missing) |
| ``run_manifest.json`` | + ``native_file``, ``native_sha256``, ``hashes``, ``finish`` |

Then optionally ``recadbench-eval score`` (only where ``RECADBENCH_GT_DIR`` exists: the evaluator
host) and/or enqueue a ``score`` job for the evaluator host. Track S (plan only) skips extraction.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .artifacts import hash_tree, sha256_file
from .gate_hook import GATE_FILE, PLAN_RE

AGENT_OUTPUTS = ("out/features.json", "features.json", "out/output.step")


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else None


def recad(backend: str, *args: str, cwd: Path, timeout: float = 1800.0) -> dict:
    """One ``recad <backend> ...`` call in a fresh process; its single JSON result."""
    cmd = [sys.executable, "-m", "recad_cli", backend, *args]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=env)
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


def finish(run_dir: str | Path, *, score: bool = False, enqueue=None,
           timeout: float = 1800.0, efficiency: dict | None = None) -> dict:
    run = Path(run_dir).resolve()
    manifest = _json(run / "run_manifest.json")
    if manifest is None:
        raise FileNotFoundError(f"{run}/run_manifest.json missing (not a composed run dir)")
    gate = _json(run / GATE_FILE) or {}
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
    native_rel = gate.get("native_file")
    native = run / native_rel if native_rel else None
    if backend == "none":
        report["skipped"] = "plan-only run (Track S)"
    elif native is None or not native.is_file():
        report["error"] = f"native file {native_rel} missing: nothing to extract"
    else:
        ex = recad(backend, "extract", str(native), "--out", str(run / "out" / "features.json"),
                   cwd=run, timeout=timeout)
        report["extract"] = {k: ex.get(k) for k in ("ok", "error", "count", "unmapped",
                                                     "exit_code") if k in ex}
        st = recad(backend, "export", str(native), "--out", str(run / "out" / "output.step"),
                   cwd=run, timeout=timeout)
        report["export"] = {k: st.get(k) for k in ("ok", "error", "solids", "volume_mm3",
                                                    "faces", "exit_code") if k in st}
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
    out = {"ok": backend == "none" or bool((report.get("export") or {}).get("ok")),
           "run_dir": str(run), "finish": report}
    if score:
        out["score"] = run_score(run, manifest["part_id"])
    if enqueue is not None:
        out["enqueued"] = enqueue({"kind": "score", "run_id": manifest["run_id"],
                                   "part_id": manifest["part_id"], "run_dir": str(run),
                                   "native_sha256": manifest.get("native_sha256")})
    return out


def run_score(run: Path, part_id: str, timeout: float = 3600.0) -> dict:
    """``recadbench-eval score`` in its own process -> <run>/result.json."""
    if not os.environ.get("RECADBENCH_GT_DIR"):
        return {"ok": False, "skipped": "RECADBENCH_GT_DIR not set (score on the evaluator host)"}
    cmd = [sys.executable, "-m", "recadbench_eval.cli", "score", "--run-dir", str(run),
           "--part", part_id]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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
