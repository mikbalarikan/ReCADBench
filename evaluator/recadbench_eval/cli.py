"""``recadbench-eval`` command line.

    recadbench-eval score --run-dir runs/<id> --part <part_id> [--out result.json] [--s5]
                          [--config eval.yaml] [--track E|T|S] [--plan plan/re_plan.v2.json]
                          [--layer official|open]
    recadbench-eval score-submission --zip submission.zip --out-dir D [--tasks-file F]
                          [--layer open|official] [--timeout 900] [--heatmap-timeout S]
                          [--config eval.yaml]
    recadbench-eval render-heatmap --run-dir runs/<id> --part <part_id> --out map.png
                          [--result private/<id>/result.json]
    recadbench-eval plan-score --plan plan/re_plan.v1.json --part <part_id>
    recadbench-eval report <result.json | dir> ... [--csv out.csv] [--md out.md]
    recadbench-eval golden --part <part_id> --out-dir <tmp> [--kinds good,lego,...]

GT is read only from ``RECADBENCH_GT_DIR``. Console output is ASCII.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import CLASS_KEYDIM_TOL, EvalConfig


def _cfg(a: argparse.Namespace) -> EvalConfig:
    over = {"s5": True} if getattr(a, "s5", False) else {}
    if getattr(a, "config", None):
        return EvalConfig.from_file(a.config, **over)
    return EvalConfig.with_overrides(**over)


def _score(a: argparse.Namespace) -> int:
    from .score import dumps, score_run, write_result

    res = score_run(a.run_dir, a.part, _cfg(a), track=a.track, plan_path=a.plan,
                    layer=a.layer)
    out = Path(a.out) if a.out else Path(a.run_dir) / "result.json"
    write_result(res, out)
    s = res["scores"]
    print(f"{res['part_id']} {res['run_id']}: gate={'ok' if res['gate']['ok'] else 'FAIL'} "
          f"total={s.get('total')} pass={res['pass']} -> {out}")
    if a.print:
        sys.stdout.write(dumps(res))
    return 0


def _score_submission(a: argparse.Namespace) -> int:
    from .submission import SubmissionError
    from .submit_score import read_tasks_file, score_submission

    ids = read_tasks_file(a.tasks_file) if a.tasks_file else None
    try:
        row = score_submission(a.zip, a.out_dir, task_ids=ids, layer=a.layer,
                               timeout=a.timeout, config_path=a.config,
                               heatmap=not a.no_heatmap, submission_id=a.submission_id,
                               heatmap_timeout=a.heatmap_timeout)
    except SubmissionError as exc:
        print(f"rejected: {exc}".encode("ascii", "replace").decode("ascii"), file=sys.stderr)
        return 2
    print(f"{row['submission_id']}: open_total={row['open_total']} gate_ok={row['n_gate_ok']}"
          f"/{len(row['per_task'])} -> {Path(a.out_dir) / 'summary.json'}")
    return 0


def _heatmap(a: argparse.Namespace) -> int:
    from .public_report import render_heatmap

    render_heatmap(a.run_dir, a.part, _cfg(a), a.out, result_path=a.result)
    return 0


def _plan(a: argparse.Namespace) -> int:
    from . import gtstore
    from .plan_score import plan_score

    part = gtstore.load_part(a.part)
    ir = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    tol = CLASS_KEYDIM_TOL[part.meta["tolerance_class"]]
    res = plan_score(ir, part.plan or {}, part.key_dims, tol, part.T_scan_to_gt)
    sys.stdout.write(json.dumps(res, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    return 0


def _report(a: argparse.Namespace) -> int:
    from .report import load, markdown, to_csv

    results = load(a.paths)
    md = markdown(results, a.title)
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8", newline="\n")
    if a.csv:
        Path(a.csv).write_text(to_csv(results), encoding="utf-8", newline="\n")
    sys.stdout.write(md)
    return 0


def _golden(a: argparse.Namespace) -> int:
    from . import brep, golden, gtstore

    part = gtstore.load_part(a.part)
    shape = brep.load_step(part.step_path)
    kinds = a.kinds.split(",") if a.kinds else list(golden.KINDS)
    for k in kinds:
        info = golden.build(k, Path(a.out_dir) / k, part_id=a.part, gt_shape=shape,
                            gt_plan=part.plan or {}, key_dims=part.key_dims, meta=part.meta,
                            T_scan_to_gt=part.T_scan_to_gt)
        print(f"{k}: {json.dumps(info['changed'], ensure_ascii=True)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recadbench-eval", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score a run dir -> result.json")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--part", help="part id (default: run_manifest.json part_id)")
    s.add_argument("--out", help="result path (default: <run-dir>/result.json)")
    s.add_argument("--track", choices=["T", "S", "E"])
    s.add_argument("--plan", help="IR to plan-score (default: highest plan/re_plan.vN.json)")
    s.add_argument("--config", help="YAML/JSON EvalConfig overrides")
    s.add_argument("--s5", action="store_true", help="score S5 from out/s5/edits.json")
    s.add_argument("--print", action="store_true", help="also print result.json")
    s.add_argument("--layer", choices=["official", "open"], default="official",
                   help="open = STEP-only scoring (ADR 0006)")
    s.set_defaults(func=_score)
    z = sub.add_parser("score-submission", help="score a submission zip -> public report")
    z.add_argument("--zip", required=True)
    z.add_argument("--out-dir", required=True)
    z.add_argument("--tasks-file", help="task ids, one per line (default: the 10 dev tasks)")
    z.add_argument("--layer", choices=["official", "open"], default="open")
    z.add_argument("--timeout", type=float, default=900.0, help="seconds per task")
    z.add_argument("--heatmap-timeout", type=float,
                   help="seconds per deviation render (default: --timeout)")
    z.add_argument("--config", help="YAML/JSON EvalConfig overrides (sealed in eval_version)")
    z.add_argument("--submission-id", help="default: sub-<sha8>")
    z.add_argument("--no-heatmap", action="store_true", help="skip the deviation renders")
    z.set_defaults(func=_score_submission)
    h = sub.add_parser("render-heatmap", help="deviation map PNG of a prediction (public)")
    h.add_argument("--run-dir", required=True)
    h.add_argument("--part", required=True)
    h.add_argument("--out", required=True)
    h.add_argument("--result", help="private result.json: reuse its alignment (skips ICP)")
    h.add_argument("--config")
    h.set_defaults(func=_heatmap)
    q = sub.add_parser("plan-score", help="score one IR against gt_plan.json")
    q.add_argument("--plan", required=True)
    q.add_argument("--part", required=True)
    q.set_defaults(func=_plan)
    r = sub.add_parser("report", help="markdown / CSV table over result.json files")
    r.add_argument("paths", nargs="+")
    r.add_argument("--csv")
    r.add_argument("--md")
    r.add_argument("--title")
    r.set_defaults(func=_report)
    g = sub.add_parser("golden", help="build golden candidates from a GT part (never commit)")
    g.add_argument("--part", required=True)
    g.add_argument("--out-dir", required=True)
    g.add_argument("--kinds")
    g.set_defaults(func=_golden)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.func(a) or 0)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        # the type name keeps empty messages (AssertionError from the kernel) diagnosable
        msg = f"error: {type(exc).__name__}" + (f": {exc}" if str(exc) else "")
        print(msg.encode("ascii", "replace").decode("ascii"), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
