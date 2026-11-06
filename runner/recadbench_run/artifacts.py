"""Hashing and ground-truth hygiene of run dirs.

* :func:`sha256_file` / :func:`hash_tree` - artifact hashes for ``run_manifest.json``.
* :func:`gt_leak_check` - nothing from ``RECADBENCH_GT_DIR`` reaches a run dir: no file whose
  bytes equal a GT file (except the scan, which IS the input: ``input/input.stl`` equals the GT
  store's ``scan_raw.stl`` by design, ADR 0002), no JSON document equal to ``gt_plan.json``
  (Track T gets the MAPPED plan only, and only at ``plan/re_plan.v1.json``), no GT key-dim file,
  no plan at all outside Track T at compose time, and no file that names the GT dir path.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

SKIP_PARTS = {"__pycache__"}
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def sha256_file(p: Path) -> str:
    st = p.stat()
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    if key not in _HASH_CACHE:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        _HASH_CACHE[key] = h.hexdigest()
    return _HASH_CACHE[key]


def files_under(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*")) if p.is_file() and not SKIP_PARTS & set(p.parts)]


def hash_tree(root: Path, rels: list[str] | None = None) -> dict[str, str]:
    """{relative POSIX path: sha256} of the given files (default: every file below root)."""
    root = Path(root)
    paths = [root / r for r in rels] if rels is not None else files_under(root)
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in paths if p.is_file()}


def _json_or_none(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def gt_leak_check(run_dir: Path, gt_dir: Path, part_id: str | None = None,
                  allow_seed: bool = False) -> list[str]:
    """Problems found (empty = clean). ``allow_seed``: Track T, a plan at plan/re_plan.v1.json
    is expected (it must still differ from the raw GT plan unless the part is synthetic with
    an identity registration, where the mapped plan equals it)."""
    run_dir, gt_dir = Path(run_dir).resolve(), Path(gt_dir).resolve()
    problems: list[str] = []
    if not gt_dir.is_dir():
        return problems
    gt_files = files_under(gt_dir)
    scan_shas = {sha256_file(p) for p in gt_files if p.name == "scan_raw.stl"}
    gt_shas = {sha256_file(p): p for p in gt_files}
    gt_plans = [d for p in gt_files if p.name == "gt_plan.json"
                if (d := _json_or_none(p)) is not None]
    identity = False
    if part_id and (gt_dir / part_id / "meta.yaml").is_file():
        try:
            import yaml

            meta = yaml.safe_load((gt_dir / part_id / "meta.yaml").read_text("utf-8"))
            identity = meta.get("source_kind") == "synthetic"
        except Exception:  # pragma: no cover - unreadable meta: stay strict
            identity = False
    gt_str = [os.path.normcase(str(gt_dir)), str(gt_dir).replace("\\", "/")]
    for p in files_under(run_dir):
        rel = p.relative_to(run_dir).as_posix()
        sha = sha256_file(p)
        if sha in gt_shas and not (sha in scan_shas and rel == "input/input.stl"):
            problems.append(f"{rel} equals GT file {gt_shas[sha].relative_to(gt_dir).as_posix()}")
            continue
        if p.name in ("gt_plan.json", "key_dims.yaml", "meta.yaml", "gt.step") or \
                p.name.startswith("gt_native."):
            problems.append(f"{rel}: GT file name")
        if p.suffix.lower() in (".json", ".md", ".yaml", ".yml", ".txt", ".py"):
            if p.stat().st_size < 4 << 20:
                text = p.read_text(encoding="utf-8", errors="replace")
                low = os.path.normcase(text)
                if any(s and (s in low or s in text) for s in gt_str):
                    problems.append(f"{rel} names the GT dir")
        if p.suffix.lower() == ".json":
            doc = _json_or_none(p)
            if doc is not None and doc in gt_plans and not (
                    allow_seed and identity and rel == "plan/re_plan.v1.json"):
                problems.append(f"{rel} is a GT plan in the GT frame")
        if rel.startswith("plan/") and rel != "plan/re_plan.v1.json" and \
                rel.endswith(".json"):
            problems.append(f"{rel}: no plan revision may exist at compose time")
        if rel == "plan/re_plan.v1.json" and not allow_seed:
            problems.append("plan/re_plan.v1.json exists outside Track T")
    return problems
