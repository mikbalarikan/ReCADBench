#!/usr/bin/env python3
"""Cross-backend conformance suite (05_TOOLS "Conformance"; backends/conformance/README.md).

    python backends/conformance/run_conformance.py [--backend b ...] [--case C01_block ...]
                                                   [--out report.json] [--work-dir DIR]

For every IR case in ir_cases/ and every selected backend whose environment is available
(`recad backends`), through the `recad` dispatcher (a subprocess per call):

  1. `recad <b> replay <case> --out-dir <work>/<b>/<case>`  → ok, exactly one solid,
     volume within expected.tolerance_rel, features.json schema-valid (dispatcher),
     `unmapped` empty, count == expected.feature_count, taxonomy types == expected.features
     (as a multiset; order is reported but not gated);
  2. `recad <b> set-param <native> <name> <value> --out …` → new volume within tolerance;
  3. CROSS-BACKEND: every backend that ran a case reports the same count and types.

Prints a table, writes the report JSON (--out), exits 1 on any failure (2: no backend available).
Environment: as `recad` (RECAD_BUILD123D_PYTHON, RECAD_FREECAD_PYTHON, RECAD_SOLIDCRAFT_CLI, …).
Work files (STEP, native models) go to --work-dir (default: a temporary directory).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKENDS_DIR = HERE.parent
CASES_DIR = HERE / "ir_cases"
ALL_BACKENDS = ("build123d", "freecad", "solidcraft", "nx")
# Runs recad_cli from this checkout without putting backends/ on PYTHONPATH (that would expose
# backends/build123d/ as a namespace package to every child interpreter).
_BOOT = (f"import sys; sys.path.insert(0, {str(BACKENDS_DIR)!r}); "
         "from recad_cli.cli import main; sys.exit(main(sys.argv[1:]))")


def recad(*args: str, timeout: float = 1800) -> tuple[int, dict[str, Any]]:
    """Run the dispatcher in a fresh interpreter; (exit code, JSON result)."""
    env = dict(os.environ)
    env.setdefault("RECAD_BACKENDS_DIR", str(BACKENDS_DIR))
    try:
        proc = subprocess.run([sys.executable, "-c", _BOOT, *args], capture_output=True,
                              text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, {"ok": False, "error": f"timeout after {timeout:g} s"}
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    try:
        res = json.loads("\n".join(lines)) if args[0] == "backends" else json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        res = {"ok": False, "error": "dispatcher printed no JSON",
               "stderr_tail": proc.stderr[-1500:]}
    if isinstance(res, dict) and not res.get("ok", True):
        res.setdefault("stderr_tail", proc.stderr[-1500:])
    return proc.returncode, res


def load_cases(only: list[str] | None) -> list[dict[str, Any]]:
    cases = []
    for d in sorted(CASES_DIR.iterdir()):
        exp = d / "expected.json"
        if exp.is_file() and (not only or d.name in only):
            cases.append(json.loads(exp.read_text()))
    return cases


def within(value: Any, target: float, rel: float) -> bool:
    return isinstance(value, int | float) and abs(value - target) <= rel * abs(target)


def rel_err(value: Any, target: float) -> float | None:
    return (value - target) / target if isinstance(value, int | float) and target else None


def run_case(backend: str, case: dict[str, Any], work: Path) -> dict[str, Any]:
    cid, tol = case["case"], case["tolerance_rel"]
    out_dir = work / backend / cid
    row: dict[str, Any] = {"backend": backend, "case": cid, "failures": []}
    fail = row["failures"].append

    rc, res = recad(backend, "replay", cid, "--out-dir", str(out_dir))
    row["replay"] = {k: res.get(k) for k in ("ok", "volume_mm3", "faces", "solids", "count",
                                             "features_valid", "error")}
    if rc != 0 or not res.get("ok"):
        fail(f"replay failed (exit {rc}): {res.get('error')}")
        return row
    feats_path = out_dir / "features.json"
    feats = json.loads(feats_path.read_text()) if feats_path.is_file() else {}
    types = [f.get("taxonomy_type") for f in feats.get("features", []) if f.get("counted", True)]
    row.update(volume_mm3=res.get("volume_mm3"), faces=res.get("faces"),
               count=feats.get("count"), types=types, unmapped=feats.get("unmapped", []),
               volume_rel_err=rel_err(res.get("volume_mm3"), case["volume_mm3"]),
               faces_match=res.get("faces") == case.get("faces"))
    if res.get("solids") != 1:
        fail(f"solids={res.get('solids')} (need 1)")
    if not within(res.get("volume_mm3"), case["volume_mm3"], tol):
        fail(f"volume {res.get('volume_mm3')} vs {case['volume_mm3']} (tol {tol:g})")
    if res.get("features_valid") is not True:
        fail("features.json not schema-valid")
    if row["unmapped"]:
        fail(f"unmapped: {row['unmapped']}")
    if feats.get("count") != case["feature_count"]:
        fail(f"count {feats.get('count')} != {case['feature_count']}")
    if Counter(types) != Counter(case["features"]):
        fail(f"types {types} != {case['features']}")
    row["types_in_ir_order"] = types == case["features"]

    sp = case["set_param"]
    natives = sorted(p for p in out_dir.glob("native.*") if p.is_file())
    if not natives:
        fail("no native.* written by replay")
        return row
    native = natives[0]
    rc, res = recad(backend, "set-param", str(native), sp["name"], repr(float(sp["value"])),
                    "--out", str(out_dir / "set_param.step"),
                    "--native-out", str(out_dir / f"set_param{native.suffix}"))
    row["set_param"] = {"name": sp["name"], "value": sp["value"], "ok": res.get("ok"),
                        "volume_after_mm3": res.get("volume_after_mm3"),
                        "expected_mm3": sp["volume_mm3"],
                        "rel_err": rel_err(res.get("volume_after_mm3"), sp["volume_mm3"]),
                        "error": res.get("error")}
    if rc != 0 or not res.get("ok"):
        fail(f"set-param failed (exit {rc}): {res.get('error')}")
    elif not within(res.get("volume_after_mm3"), sp["volume_mm3"], tol):
        fail(f"set-param volume {res.get('volume_after_mm3')} vs {sp['volume_mm3']}")
    return row


def cross_backend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per case: every backend that produced a count must report the same count and types."""
    out = []
    for cid in sorted({r["case"] for r in rows}):
        got = {r["backend"]: (r.get("count"), sorted(r.get("types") or []))
               for r in rows if r["case"] == cid and r.get("count") is not None}
        same = len({json.dumps(v) for v in got.values()}) <= 1
        out.append({"case": cid, "counts": {b: v[0] for b, v in got.items()},
                    "identical": same})
    return out


def fmt_cell(r: dict[str, Any] | None) -> str:
    if r is None:
        return "-"
    err = r.get("volume_rel_err")
    v = f"{err * 100:+.4f}%" if err is not None else "n/a"
    spr = (r.get("set_param") or {}).get("rel_err")
    sp = "sp ok" if spr is not None and not any("set-param" in f for f in r["failures"]) \
        else "sp FAIL"
    return f"{'PASS' if not r['failures'] else 'FAIL'} {v} n={r.get('count')} {sp}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--backend", action="append", choices=ALL_BACKENDS,
                    help="backend to run (repeatable; default: every available one)")
    ap.add_argument("--case", action="append", help="case id to run (repeatable; default all)")
    ap.add_argument("--out", help="write the report JSON here")
    ap.add_argument("--work-dir", help="keep replay outputs here (default: temporary dir)")
    a = ap.parse_args(argv)

    _, infos = recad("backends", "--json", timeout=600)
    info = {i["backend"]: i for i in infos} if isinstance(infos, list) else {}
    wanted = a.backend or list(ALL_BACKENDS)
    run_b = [b for b in wanted if info.get(b, {}).get("available")]
    skipped = {b: info.get(b, {}).get("reason", "unknown") for b in wanted if b not in run_b}
    for b, why in skipped.items():
        print(f"skip {b}: {why}", file=sys.stderr)
    if not run_b:
        print("no backend available", file=sys.stderr)
        return 2
    cases = load_cases(a.case)

    with tempfile.TemporaryDirectory(prefix="recad-conformance-") as tmp:
        work = Path(a.work_dir or tmp).resolve()

        def per_backend(b: str) -> list[dict[str, Any]]:
            rows = []
            for c in cases:
                r = run_case(b, c, work)
                print(f"{b:11} {c['case']:22} {fmt_cell(r)}"
                      + (f"  <- {'; '.join(r['failures'])}" if r["failures"] else ""),
                      file=sys.stderr, flush=True)
                rows.append(r)
            return rows

        with ThreadPoolExecutor(max_workers=len(run_b)) as ex:
            rows = [r for rs in ex.map(per_backend, run_b) for r in rs]

    xb = cross_backend(rows)
    by = {(r["backend"], r["case"]): r for r in rows}
    summary = {b: {"passed": sum(not by[(b, c["case"])]["failures"] for c in cases),
                   "total": len(cases), "version": info[b].get("version")} for b in run_b}
    ok = all(not r["failures"] for r in rows) and all(x["identical"] for x in xb)

    w = max(len(c["case"]) for c in cases)
    print(f"{'case':{w}} | " + " | ".join(f"{b:34}" for b in run_b) + " | cross-backend")
    for c in cases:
        x = next(x for x in xb if x["case"] == c["case"])
        print(f"{c['case']:{w}} | "
              + " | ".join(f"{fmt_cell(by.get((b, c['case']))):34}" for b in run_b)
              + f" | {'identical' if x['identical'] else 'DIFFERENT ' + str(x['counts'])}")
    print("passed: " + ", ".join(f"{b} {s['passed']}/{s['total']}" for b, s in summary.items())
          + f"; cross-backend {'OK' if all(x['identical'] for x in xb) else 'FAIL'}"
          + f" -> {'PASS' if ok else 'FAIL'}")

    report = {"date": dt.date.today().isoformat(), "ok": ok, "backends": summary,
              "skipped": skipped, "cases": [c["case"] for c in cases], "rows": rows,
              "cross_backend": xb}
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8",
                               newline="\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
