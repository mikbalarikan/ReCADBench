#!/usr/bin/env python3
"""Check a ReCADBench Open submission.zip before uploading it (docs/benchmark/submission.md).

    python tools/sanity_check_submission.py submission.zip
    python tools/sanity_check_submission.py submission.zip --inputs path/to/tasks   # <id>/input.stl
    python tools/sanity_check_submission.py submission.zip --tasks-file splits/dev.txt

Structure checks (zip layout, limits, meta.json) need only the Python standard library. If
build123d / OCP is installed, each output.step is also loaded and checked: exactly one solid,
valid B-rep and, with ``--inputs``, a bounding-box diagonal within 20 % of the scan's.

Exit code 0 = no errors (warnings may be printed), 1 = errors, 2 = bad command line.
The scan-size check is coarse on purpose; it does not tell you anything about your score.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import struct
import sys
import tempfile
from pathlib import Path

# The 10 public dev tasks (ReCADBench-data/splits/dev.txt).
TASK_IDS = (
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
BBOX_TOL = 0.20


def _load_submission_module():
    """recadbench_eval.submission from: an installed evaluator, this repo checkout
    (<repo>/evaluator), or a copy named submission.py next to this script."""
    try:
        from recadbench_eval import submission

        return submission
    except ImportError:
        pass
    here = Path(__file__).resolve().parent
    evaluator = here.parent / "evaluator"
    if (evaluator / "recadbench_eval" / "submission.py").is_file():
        sys.path.insert(0, str(evaluator))
        from recadbench_eval import submission

        return submission
    local = here / "submission.py"
    if local.is_file():
        spec = importlib.util.spec_from_file_location("rcb_submission", local)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["rcb_submission"] = mod  # dataclasses resolve the module by name
        spec.loader.exec_module(mod)
        return mod
    raise SystemExit("error: cannot find recadbench_eval/submission.py (run from a repo checkout"
                     " or put submission.py next to this script)")


# ------------------------------------------------------------------------------------ STL bbox
def stl_bbox(path: Path) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Axis-aligned bounding box of a binary or ASCII STL (standard library only)."""
    data = path.read_bytes()
    lo = [math.inf] * 3
    hi = [-math.inf] * 3

    def add(x: float, y: float, z: float) -> None:
        for i, v in enumerate((x, y, z)):
            lo[i] = min(lo[i], v)
            hi[i] = max(hi[i], v)

    n = struct.unpack_from("<I", data, 80)[0] if len(data) >= 84 else -1
    if n >= 0 and len(data) == 84 + 50 * n:
        for k in range(n):
            vals = struct.unpack_from("<12f", data, 84 + 50 * k)
            for j in (3, 6, 9):
                add(*vals[j:j + 3])
    else:
        for line in data.decode("ascii", "replace").splitlines():
            tok = line.split()
            if len(tok) == 4 and tok[0] == "vertex":
                add(float(tok[1]), float(tok[2]), float(tok[3]))
    if lo[0] == math.inf:
        raise ValueError("no triangles")
    return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])


def _diag(lo, hi) -> float:
    return math.dist(lo, hi)


# ------------------------------------------------------------------------------------ geometry
def _kernel_available() -> bool:
    return (importlib.util.find_spec("build123d") is not None
            and importlib.util.find_spec("OCP") is not None)


def check_step(step: Path, input_stl: Path | None) -> list[str]:
    """Errors for one STEP (empty = ok). Needs build123d / OCP."""
    from build123d import import_step
    from OCP.BRepCheck import BRepCheck_Analyzer

    try:
        shape = import_step(str(step))
    except Exception as exc:  # noqa: BLE001 - any kernel failure is a load failure
        return [f"STEP does not load ({type(exc).__name__})"]
    if shape is None or shape.wrapped is None or shape.wrapped.IsNull():
        return ["STEP is empty"]
    errors = []
    solids = list(shape.solids())
    if len(solids) != 1:
        errors.append(f"expected exactly one solid, got {len(solids)}")
    if not BRepCheck_Analyzer(shape.wrapped).IsValid():
        errors.append("B-rep is not valid (BRepCheck)")
    if input_stl is not None and solids:
        bb = shape.bounding_box()
        d_step = _diag((bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z))
        try:
            d_scan = _diag(*stl_bbox(input_stl))
        except (ValueError, struct.error, OSError) as exc:
            errors.append(f"input.stl unreadable ({exc})")
        else:
            rel = abs(d_step - d_scan) / d_scan if d_scan > 0 else math.inf
            if rel > BBOX_TOL:
                errors.append(f"bbox diagonal {d_step:.1f} mm vs scan {d_scan:.1f} mm"
                              f" ({rel:.0%} off, limit {BBOX_TOL:.0%}); wrong units or frame?")
    return errors


# ------------------------------------------------------------------------------------ main
def _task_ids(args) -> list[str]:
    if args.tasks_file:
        lines = Path(args.tasks_file).read_text(encoding="utf-8").splitlines()
        ids = [s.strip() for s in lines if s.strip() and not s.strip().startswith("#")]
        if not ids:
            raise SystemExit(f"error: no task ids in {args.tasks_file}")
        return ids
    if args.inputs:
        ids = sorted(d.name for d in Path(args.inputs).iterdir()
                     if (d / "input.stl").is_file())
        if ids:
            return ids
        print(f"note: no <id>/input.stl under {args.inputs}; using the built-in task list")
    return list(TASK_IDS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("zip", help="submission.zip")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--tasks-file", help="text file with one task id per line")
    grp.add_argument("--inputs", help="folder with <task_id>/input.stl (enables the bbox check)")
    ap.add_argument("--no-geometry", action="store_true", help="structure checks only")
    args = ap.parse_args(argv)

    sm = _load_submission_module()
    task_ids = _task_ids(args)
    try:
        sub = sm.inspect_zip(args.zip, task_ids)
    except sm.SubmissionError as exc:
        print(f"ERROR  {exc}")
        print("RESULT: FAIL (structure)")
        return 1

    print(f"sha256 {sub.sha256}")
    name = f"{sub.meta['submission_name']} by {sub.meta['submitter_name']}"
    print("meta   " + name.encode("ascii", "backslashreplace").decode("ascii"))
    print(f"tasks  {len(sub.tasks)} of {len(task_ids)} with output.step"
          f" ({len(sub.missing)} missing, scored 0)")
    for w in sub.warnings:
        print(f"WARN   {w}")

    n_err = 0
    if args.no_geometry:
        print("note: geometry checks skipped (--no-geometry)")
    elif not _kernel_available():
        print("note: build123d/OCP not installed; geometry checks skipped"
              " (pip install build123d to enable)")
    elif sub.tasks:
        with tempfile.TemporaryDirectory(prefix="rcb_sanity_") as tmp:
            for tid in sub.tasks:
                try:
                    step = sm.extract_task(args.zip, sub, tid, Path(tmp) / tid)
                except sm.SubmissionError as exc:
                    errs = [str(exc)]
                else:
                    stl = Path(args.inputs) / tid / "input.stl" if args.inputs else None
                    errs = check_step(step, stl if stl and stl.is_file() else None)
                for e in errs:
                    print(f"ERROR  {tid}: {e}")
                n_err += bool(errs)
                if not errs:
                    print(f"ok     {tid}")
    if n_err:
        print(f"RESULT: FAIL ({n_err} task(s) with errors)")
        return 1
    print("RESULT: OK" + (" (with warnings)" if sub.warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
