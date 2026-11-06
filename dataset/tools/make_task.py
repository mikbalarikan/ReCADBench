#!/usr/bin/env python3
"""Package GT parts into agent-visible inputs (ADR 0002). Runs on the evaluator host.

    python dataset/tools/make_task.py --gt-root <recadbench-gt>/parts \
        --data-root <recadbench-data> [part_id ...]

For each part (default: every folder under --gt-root with a meta.yaml):
  scan parts      → data/parts/<id>/input.stl = byte-identical copy of the GT folder's scan
                    (sha256 checked against meta.yaml), task.yaml (input_kind: scan)
  synthetic parts → input.stl tessellated from gt.step with meta.yaml tessellation_chord_mm
                    (needs build123d), task.yaml (input_kind: synthetic_clean)
Then rewrites data/MANIFEST.json (sha256 per file) and data/splits/{dev,test}.txt.

Only the scan (or synthetic mesh) and task.yaml ever leave the GT folder — never gt.step,
gt_plan.json, key_dims.yaml, meta.yaml, masks or reports. Every task.yaml is validated with
recadbench-spec before it is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml

SPEC_VERSION = "0.3.0"  # ADR 0004
DEFAULT_BUDGETS = {"max_wall_s": 3600, "max_turns": 200}
DELIVERABLES = ["out/output.step", "cad/native", "plan/re_plan.vN.json"]
# scan metadata that may be shown to the agent (what a customer would tell you)
AGENT_VISIBLE_SCAN_KEYS = ("scanner", "mode", "sessions", "known_defects")
INSTRUCTIONS = (
    "Reverse-engineer input/input.stl (a raw 3D scan in the scanner frame) into one clean, "
    "parametric solid that captures the design intent. Write plan/re_plan.v1.json before any CAD "
    "call, then deliver out/output.step and the native CAD model."
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _validate(doc: dict, schema: str) -> None:
    from recadbench_spec import validate_document

    report = validate_document(doc, schema)
    if not report.ok:
        raise SystemExit(f"{schema} invalid: " + "; ".join(str(e) for e in report.errors))


def _mesh_facts(path: Path) -> dict:
    try:
        import trimesh
    except ImportError:  # pragma: no cover
        return {}
    m = trimesh.load(path, force="mesh", process=False)
    return {"triangles": int(len(m.faces)), "watertight": bool(m.is_watertight)}


def build_task(meta: dict, input_kind: str, mesh_facts: dict, budgets: dict) -> dict:
    task = {
        "spec_version": SPEC_VERSION,
        "part_id": meta["part_id"],
        "input_stl": "input/input.stl",
        "input_kind": input_kind,
    }
    if input_kind == "scan":
        src = meta.get("scan", {})
        scan = {k: src[k] for k in AGENT_VISIBLE_SCAN_KEYS if k in src}
        scan.setdefault("scanner", "unknown")
        scan.update(mesh_facts)
        scan["frame"] = "scanner"
        task["scan"] = scan
    task.update(
        {
            "units": "mm",
            "tolerance_class": meta["tolerance_class"],
            "tier": meta["tier"],
            "feature_budget_hint": None,
            "budgets": dict(budgets),
            "deliverables": list(DELIVERABLES),
            "instructions": INSTRUCTIONS,
        }
    )
    return task


def make_part(gt_part: Path, data_root: Path, budgets: dict) -> dict:
    meta = yaml.safe_load((gt_part / "meta.yaml").read_text(encoding="utf-8"))
    _validate(meta, "gt_meta")
    pid = meta["part_id"]
    if pid != gt_part.name:
        raise SystemExit(f"{gt_part}: folder name must equal part_id {pid!r}")
    out = data_root / "parts" / pid
    out.mkdir(parents=True, exist_ok=True)
    target = out / "input.stl"

    if meta["source_kind"] == "synthetic":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import scanreg

        mesh = scanreg.load_mesh(gt_part / "gt.step", chord_mm=meta["tessellation_chord_mm"])
        mesh.export(target, file_type="stl")
        kind = "synthetic_clean"
    else:
        src = gt_part / meta["scan"]["file"]
        digest = sha256(src)
        if digest != meta["scan"]["sha256"]:
            raise SystemExit(
                f"{src}: sha256 {digest[:12]}... does not match meta.yaml - re-run register_gt.py"
            )
        shutil.copyfile(src, target)
        kind = "scan"

    task = build_task(meta, kind, _mesh_facts(target) if kind == "scan" else {}, budgets)
    _validate(task, "task")
    (out / "task.yaml").write_text(
        yaml.safe_dump(task, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",  # LF on every OS: MANIFEST.json hashes these bytes
    )
    return {"part_id": pid, "split": meta["split"], "input_kind": kind}


def write_manifest_and_splits(data_root: Path, splits: dict[str, str]) -> None:
    sp = data_root / "splits"
    sp.mkdir(exist_ok=True)
    current: dict[str, str] = {}
    for name in ("dev", "test"):
        f = sp / f"{name}.txt"
        if f.exists():
            for line in f.read_text().split():
                current[line] = name
    current.update(splits)
    for name in ("dev", "test"):
        ids = sorted(pid for pid, s in current.items() if s == name)
        (sp / f"{name}.txt").write_text("".join(f"{i}\n" for i in ids), newline="\n")
    files = {}
    for f in sorted((data_root / "parts").rglob("*")):
        if f.is_file():
            files[f.relative_to(data_root).as_posix()] = {
                "sha256": sha256(f),
                "bytes": f.stat().st_size,
            }
    (data_root / "MANIFEST.json").write_text(
        json.dumps({"spec_version": SPEC_VERSION, "files": files}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--gt-root", required=True, help="recadbench-gt/parts")
    p.add_argument("--data-root", required=True, help="recadbench-data checkout")
    p.add_argument("--max-wall-s", type=int, default=DEFAULT_BUDGETS["max_wall_s"])
    p.add_argument("--max-turns", type=int, default=DEFAULT_BUDGETS["max_turns"])
    p.add_argument("parts", nargs="*", help="part ids (default: all with meta.yaml)")
    args = p.parse_args(argv)

    gt_root, data_root = Path(args.gt_root).resolve(), Path(args.data_root).resolve()
    public = Path(__file__).resolve().parents[2]
    if data_root == public or public in data_root.parents:
        raise SystemExit("refusing to write task data inside the public ReCADBench repo")
    ids = args.parts or sorted(d.name for d in gt_root.iterdir() if (d / "meta.yaml").is_file())
    budgets = {"max_wall_s": args.max_wall_s, "max_turns": args.max_turns}
    done = [make_part(gt_root / pid, data_root, budgets) for pid in ids]
    write_manifest_and_splits(data_root, {d["part_id"]: d["split"] for d in done})
    for d in done:
        print(
            f"{d['part_id']}: {d['input_kind']} -> {data_root / 'parts' / d['part_id']} "
            f"({d['split']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
