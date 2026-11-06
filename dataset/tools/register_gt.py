#!/usr/bin/env python3
"""GT-authoring step for scan parts (ADR 0002): register the scan to gt.step, then measure the
noise floor and observability, and write them into the part's meta.yaml.

    python dataset/tools/register_gt.py <recadbench-gt>/parts/<part_id> [--init pca|identity|T.json]
        [--gt-mesh gt_cad.stl] [--threshold 0.5] [--seed 11] [--write]

Reads  : <part>/meta.yaml (needs scan.file), <part>/<scan.file>, <part>/gt.step (or --gt-mesh)
Writes : with --write — meta.yaml (scan.sha256/triangles, registration, scan_floor, observability),
         <part>/registration_report.json, <part>/observability_mask.npz
Without --write it only prints the result (dry run).

Runs on the evaluator host only; it never touches recadbench-data or this public repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scanreg  # noqa: E402

DEFAULT_THRESHOLD_MM = 0.5


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_init(arg: str, scan, gt, seed: int) -> tuple[np.ndarray, str]:
    if arg == "identity":
        return np.eye(4), "identity"
    if arg == "pca":
        T = scanreg.coarse_icp(scan, gt, scanreg.coarse_pca(scan, gt, seed=seed), seed=seed)
        return T, "PCA axes (24 proper flips) + coarse ICP (20/8/3 mm caps)"
    data = json.loads(Path(arg).read_text(encoding="utf-8"))
    T = data.get("T_scan_to_gt", data.get("T", data)) if isinstance(data, dict) else data
    T = np.asarray(T, dtype=float)
    if T.shape != (4, 4):
        raise SystemExit(f"{arg}: expected a 4x4 matrix (keys T_scan_to_gt or T)")
    return T, f"initial transform from {Path(arg).name}"


def run(args: argparse.Namespace) -> dict:
    part = Path(args.part_dir)
    meta_path = part / "meta.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    if meta.get("source_kind") == "synthetic":
        raise SystemExit("source_kind=synthetic: nothing to register")
    scan_file = part / meta["scan"]["file"]
    gt_file = Path(args.gt_mesh) if args.gt_mesh else part / "gt.step"
    digest = sha256(scan_file)
    known = meta["scan"].get("sha256") not in (None, "", "TODO")
    if known and meta["scan"]["sha256"] != digest:
        raise SystemExit(
            f"meta.yaml scan.sha256 {meta['scan']['sha256'][:12]}… does not match {scan_file.name} "
            f"({digest[:12]}…): refusing to run"
        )

    scan = scanreg.load_mesh(scan_file)
    gt = scanreg.load_mesh(gt_file, chord_mm=args.chord)
    T0, init_desc = load_init(args.init, scan, gt, args.seed)
    reg = scanreg.icp(scan, gt, T0, seed=args.seed, n=args.icp_samples)
    fl = scanreg.floor_and_observability(
        scan,
        gt,
        reg.T,
        threshold_mm=args.threshold,
        seed=args.seed,
        n_scan=args.samples,
        n_gt=args.samples,
        extraneous_mm=args.extraneous,
    )
    ang, mm = scanreg.rotation_delta(np.linalg.inv(T0) @ reg.T)
    return {
        "part_id": meta["part_id"],
        "scan": {
            "file": scan_file.name,
            "sha256": digest,
            "triangles": int(len(scan.faces)),
            "watertight": bool(scan.is_watertight),
        },
        "gt_mesh": {"file": gt_file.name, "triangles": int(len(gt.faces))},
        "registration": {
            "T_scan_to_gt": [[float(v) for v in row] for row in reg.T],
            "method": f"{init_desc} + trimmed point-to-plane ICP (scan→GT, {args.icp_samples} pts)",
            "rmse_mm": round(reg.rmse_mm, 5),
            "converged": reg.converged,
            "seed": args.seed,
            "iterations": reg.iterations,
        },
        "icp_refinement_from_init": {"deg": ang, "mm": mm, "kept_fraction": reg.kept_fraction},
        "scan_floor": {
            "scan_to_gt_p50_mm": round(fl.scan_to_gt["p50"], 5),
            "scan_to_gt_p95_mm": round(fl.scan_to_gt["p95"], 5),
            "gt_to_scan_observable_p95_mm": round(
                fl.gt_to_scan_observable.get("p95", float("nan")), 5
            ),
            "max_mm": round(fl.scan_to_gt["max"], 5),
        },
        "observability": {
            "observable_fraction": round(fl.observable_fraction, 5),
            "threshold_mm": fl.threshold_mm,
            "mask_file": "observability_mask.npz",
        },
        "extraneous": {"fraction": round(fl.extraneous_fraction, 5), "beyond_mm": fl.extraneous_mm},
        "detail": {
            "scan_to_gt_all": fl.scan_to_gt_all,
            "scan_to_gt": fl.scan_to_gt,
            "gt_to_scan_all": fl.gt_to_scan_all,
            "gt_to_scan_observable": fl.gt_to_scan_observable,
            "masks": fl.masks,
            "icp_history": reg.history,
        },
        "_mask": (fl.gt_points, fl.observable),
    }


def write(part: Path, result: dict) -> None:
    meta_path = part / "meta.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    scan_meta = meta.setdefault("scan", {})
    known = scan_meta.get("sha256") not in (None, "", "TODO")
    if known and scan_meta["sha256"] != result["scan"]["sha256"]:
        raise SystemExit(
            f"meta.yaml scan.sha256 {scan_meta['sha256'][:12]}… does not match "
            f"{result['scan']['file']} "
            f"({result['scan']['sha256'][:12]}…): refusing to overwrite"
        )
    scan_meta["sha256"] = result["scan"]["sha256"]
    scan_meta["triangles"] = result["scan"]["triangles"]
    for key in ("registration", "scan_floor", "observability"):
        meta[key] = result[key]
    meta_path.write_text(
        yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    pts, obs = result["_mask"]
    np.savez_compressed(
        part / "observability_mask.npz", gt_points=pts.astype(np.float32), observable=obs
    )
    report = {k: v for k, v in result.items() if k != "_mask"}
    (part / "registration_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def validate(part: Path) -> list[str]:
    try:
        from recadbench_spec import validate_file
    except ImportError:
        return ["recadbench-spec not installed: meta.yaml not validated"]
    report = validate_file(part / "meta.yaml")
    return [str(i) for i in report.issues]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("part_dir")
    p.add_argument(
        "--init", default="identity", help="identity | pca | path to JSON 4x4 (default identity)"
    )
    p.add_argument("--gt-mesh", help="CAD-exported mesh to use instead of tessellating gt.step")
    p.add_argument("--chord", type=float, default=0.01, help="STEP tessellation chord, mm")
    p.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD_MM, help="observability distance, mm"
    )
    p.add_argument(
        "--extraneous",
        type=float,
        default=1.5,
        help="scan farther than this from GT (table, fixtures) is excluded from the floor, mm",
    )
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--icp-samples", type=int, default=20000)
    p.add_argument("--samples", type=int, default=100000, help="samples for floor/observability")
    p.add_argument("--write", action="store_true", help="update meta.yaml and write report + mask")
    args = p.parse_args(argv)

    result = run(args)
    printable = {k: v for k, v in result.items() if k not in ("_mask", "detail")}
    print(json.dumps(printable, indent=2))
    if not result["registration"]["converged"]:
        print(
            "WARNING: ICP did not converge — try --init pca or a manual initial transform",
            file=sys.stderr,
        )
    if args.write:
        write(Path(args.part_dir), result)
        problems = validate(Path(args.part_dir))
        for msg in problems:
            print(f"meta.yaml: {msg}", file=sys.stderr)
        print(
            f"wrote {Path(args.part_dir) / 'meta.yaml'}, registration_report.json, "
            "observability_mask.npz"
        )
    return 0 if result["registration"]["converged"] else 1


if __name__ == "__main__":
    sys.exit(main())
