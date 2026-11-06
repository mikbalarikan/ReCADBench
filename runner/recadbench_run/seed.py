"""Track T seed plans (evaluator host only; 00_DECISIONS D7).

``recadbench seed-plan --part <id> --out seed_plan.json`` reads ``gt_plan.json`` and
``meta.yaml: registration.T_scan_to_gt`` below ``RECADBENCH_GT_DIR`` and writes the plan mapped
into the scanner frame with ``inv(T_scan_to_gt)`` (:func:`frames.seed_from_gt`). The orchestrator
ships that document inside the Track T job payload; workers never see the GT store. Dev parts
only (``meta.split``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from .frames import seed_from_gt

ENV = "RECADBENCH_GT_DIR"


class SeedError(RuntimeError):
    pass


def gt_root() -> Path:
    v = os.environ.get(ENV)
    if not v or not Path(v).is_dir():
        raise SeedError(f"{ENV} is not set to a directory (seed plans are made on the evaluator "
                        "host only)")
    return Path(v)


def seed_plan_for(part_id: str) -> tuple[dict, list[str]]:
    folder = gt_root() / part_id
    plan_p, meta_p = folder / "gt_plan.json", folder / "meta.yaml"
    if not plan_p.is_file() or not meta_p.is_file():
        raise SeedError(f"{part_id}: gt_plan.json / meta.yaml missing under ${ENV}")
    meta = yaml.safe_load(meta_p.read_text(encoding="utf-8"))
    if meta.get("split") != "dev":
        raise SeedError(f"{part_id} is split {meta.get('split')!r}: Track T is dev-only")
    gt_plan = json.loads(plan_p.read_text(encoding="utf-8"))
    T = None
    if meta.get("source_kind") != "synthetic":
        T = (meta.get("registration") or {}).get("T_scan_to_gt")
        if T is None:
            raise SeedError(f"{part_id}: scan part without registration.T_scan_to_gt")
    plan, notes = seed_from_gt(gt_plan, T)
    from recadbench_spec import validate_document

    rep = validate_document(plan, "re_plan_ir")
    if not rep.ok:
        raise SeedError("mapped plan is not valid: " + "; ".join(str(e) for e in rep.errors[:3]))
    return plan, notes


def main_seed(a) -> int:
    try:
        plan, notes = seed_plan_for(a.part)
    except SeedError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "out": str(a.out), "notes": notes}))
    return 0
