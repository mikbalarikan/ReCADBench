"""Score a submission zip: ``recadbench-eval score-submission`` (ADR 0006, Open layer).

    score_submission("submission.zip", "out/", layer="open", timeout=900)

Per task id: ``submission.extract_task`` -> ``D/runs/<id>/`` + ``run_manifest.json``
(``run_id = sub-<sha8>-<id>``, track E), then ``python -m recadbench_eval.cli score ... --layer``
in a CHILD process with a wall-clock timeout (and RLIMIT_AS on POSIX), so a hostile or broken
STEP can neither hang nor take down the scorer. A timeout / crash is an ``error`` row with a
fixed public reason.

Writes::

    D/runs/<id>/...                 extracted inputs (private)
    D/private/<id>/result.json      full result (PRIVATE: GT-derived values)
    D/private/<id>/error.txt        child stderr tail on failure (PRIVATE)
    D/public/report.json            public row (allowlist, public_report.public_row)
    D/public/report.html
    D/public/renders/<id>.png       optional deviation map of the prediction
    D/summary.json                  = the public row
    D/error.json                    only when the zip itself is rejected (SubmissionError)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import EvalConfig
from .public_report import (
    STATUS_SENTENCES,
    declared_entry,
    empty_task,
    public_row,
    public_task,
    write_public,
)
from .submission import SubmissionError, extract_task, inspect_zip

# the 10 public dev tasks (ReCADBench-data/splits/dev.txt; same set as
# tools/sanity_check_submission.py TASK_IDS)
DEFAULT_TASK_IDS = (
    "r385_hs01_motor_mount",
    "r385_dr02_wobble_plate",
    "r385_hs04_pump_head",
    "r385_hs02_diaphragm_frame",
    "r385_dr01_eccentric_hub",
    "od_g04_brewing_gasket_support",
    "od_h11_thermoblock",
    "od_h22_three_way_valve",
    "od_h24_flowmeter",
    "od_s03_steam_knob",
)
LAYERS = ("official", "open")
MEM_LIMIT_BYTES = 4 * 2**30
STDERR_TAIL = 4000
# task ids become path components that are rmtree'd (runs/<id>, private/<id>)
TASK_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def check_task_ids(ids) -> list[str]:
    """``ids`` as a list; ValueError on an id that is not a safe single path component."""
    out = [str(t) for t in ids]
    bad = [t for t in out if t in (".", "..") or not TASK_ID_RE.fullmatch(t)]
    if bad:
        raise ValueError(f"invalid task id(s): {', '.join(repr(t) for t in bad[:5])}")
    return out


def read_tasks_file(path: str | Path) -> list[str]:
    """One task id per line; blank lines and ``#`` comments ignored; order kept, deduped."""
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        tid = line.split("#", 1)[0].strip()
        if tid and tid not in out:
            out.append(tid)
    if not out:
        raise ValueError("tasks file lists no task ids")
    return check_task_ids(out)


def load_config(config_path: str | Path | None) -> EvalConfig:
    return EvalConfig.from_file(config_path) if config_path else EvalConfig()


def config_hash(cfg: EvalConfig) -> str:
    blob = json.dumps(cfg.as_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("ascii")).hexdigest()[:8]


def eval_version(cfg: EvalConfig, layer: str = "open") -> str:
    """``<evaluator version>+<layer>.<config hash>``, e.g. ``0.1.0+open.1a2b3c4d``."""
    return f"{__version__}+{layer}.{config_hash(cfg)}"


def _limit_memory() -> None:  # pragma: no cover - runs in the POSIX child only
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT_BYTES, MEM_LIMIT_BYTES))


def run_child(args: list[str], timeout: float, python: str | None = None
              ) -> tuple[str, str]:
    """Run ``python -m recadbench_eval.cli <args>``; returns ``(outcome, stderr_tail)`` with
    outcome ``ok`` | ``timeout`` | ``crash``."""
    cmd = [python or sys.executable, "-m", "recadbench_eval.cli", *args]
    kw: dict = {}
    if os.name == "posix":
        kw["preexec_fn"] = _limit_memory
    env = dict(os.environ, PYTHONIOENCODING="ascii:replace")
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, encoding="ascii",
                            errors="replace", timeout=timeout, env=env, check=False, **kw)
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr or ""
        if isinstance(err, bytes):
            err = err.decode("ascii", "replace")
        return "timeout", err[-STDERR_TAIL:]
    if cp.returncode != 0:
        return "crash", (cp.stderr or "")[-STDERR_TAIL:]
    return "ok", (cp.stderr or "")[-STDERR_TAIL:]


def _reset(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def score_submission(zip_path: str | Path, out_dir: str | Path, *,
                     task_ids: list[str] | None = None, layer: str = "open",
                     timeout: float = 900, config_path: str | Path | None = None,
                     heatmap: bool = True, submission_id: str | None = None,
                     python: str | None = None, heatmap_timeout: float | None = None,
                     log=print) -> dict:
    """Score every task of a submission; returns the public row (also ``D/summary.json``).
    Raises ``SubmissionError`` when the zip itself is unusable (``D/error.json`` written) and
    ``ValueError`` on an unsafe task id. ``heatmap_timeout`` (default ``timeout``) bounds the
    deviation render child, which reuses the score child's alignment (no second ICP)."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}")
    ids = check_task_ids(task_ids or DEFAULT_TASK_IDS)
    h_timeout = timeout if heatmap_timeout is None else heatmap_timeout
    cfg = load_config(config_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "error.json").unlink(missing_ok=True)
    try:
        sub = inspect_zip(zip_path, ids)
    except SubmissionError as exc:
        (out / "error.json").write_bytes(
            (json.dumps({"error": str(exc)}, ensure_ascii=True) + "\n").encode("ascii"))
        raise
    sha8 = sub.sha256[:8]
    sid = submission_id or f"sub-{sha8}"
    public = out / "public"
    renders = public / "renders"
    do_heatmap = heatmap and importlib.util.find_spec("matplotlib") is not None
    cfg_args = ["--config", str(Path(config_path).resolve())] if config_path else []
    per_task: dict[str, dict] = {}
    declared: dict[str, dict] = {}
    for w in sub.warnings:
        log(f"warning: {w}")
    for tid in ids:
        run_dir = out / "runs" / tid
        priv = out / "private" / tid
        for p in (run_dir, priv, renders / f"{tid}.png"):
            _reset(p)
        if tid not in sub.tasks:
            per_task[tid] = empty_task("missing", STATUS_SENTENCES["missing"])
            log(f"{tid}: missing")
            continue
        priv.mkdir(parents=True)
        try:
            extract_task(zip_path, sub, tid, run_dir)
        except SubmissionError as exc:
            (priv / "error.txt").write_text(str(exc) + "\n", encoding="ascii", errors="replace")
            per_task[tid] = empty_task("error", STATUS_SENTENCES["extract"])
            log(f"{tid}: error (extract)")
            continue
        manifest = {"part_id": tid, "run_id": f"sub-{sha8}-{tid}", "track": "E"}
        (run_dir / "run_manifest.json").write_bytes(
            (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("ascii"))
        res_p = priv / "result.json"
        outcome, err = run_child(["score", "--run-dir", str(run_dir.resolve()), "--part", tid,
                                  "--out", str(res_p.resolve()), "--layer", layer, *cfg_args],
                                 timeout, python)
        res = None
        if outcome == "ok":
            try:
                res = json.loads(res_p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                outcome = "crash"
        if res is None:
            (priv / "error.txt").write_text(f"{outcome}\n{err}", encoding="ascii",
                                            errors="replace")
            per_task[tid] = empty_task("error", STATUS_SENTENCES[outcome])
            log(f"{tid}: error ({outcome})")
            continue
        per_task[tid] = public_task(res)
        if (d := declared_entry(res)) is not None:
            declared[tid] = d
        log(f"{tid}: {per_task[tid]['status']} total={per_task[tid]['total']}")
        if do_heatmap and per_task[tid]["status"] == "ok":
            png = renders / f"{tid}.png"
            h_out, h_err = run_child(["render-heatmap", "--run-dir", str(run_dir.resolve()),
                                      "--part", tid, "--out", str(png.resolve()),
                                      "--result", str(res_p.resolve()), *cfg_args],
                                     h_timeout, python)
            if h_out != "ok":
                _reset(png)
                (priv / "heatmap_error.txt").write_text(f"{h_out}\n{h_err}", encoding="ascii",
                                                        errors="replace")
    row = public_row(submission_id=sid, sha256=sub.sha256, eval_version=eval_version(cfg, layer),
                     layer=layer, meta=sub.meta, task_ids=ids, per_task=per_task,
                     declared=declared)
    write_public(public, row)
    (out / "summary.json").write_bytes((public / "report.json").read_bytes())
    return row
