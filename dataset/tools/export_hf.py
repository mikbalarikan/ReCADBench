#!/usr/bin/env python3
"""Stage the ReCADBench Open datasets for the Hugging Face Hub (inputs public-ready, GT private).

    python dataset/tools/export_hf.py --data-root <data> --gt-root <recadbench-gt>/parts \
        --out <STAGING> [--parts id ... | --split dev] [--previews DIR] \
        [--push --inputs-repo ORG/recadbench-inputs --gt-repo ORG/recadbench-gt [--inputs-public]]

Builds two local folders and, only with --push, uploads them:

  STAGING/inputs/  parts/<id>/{input.stl, task.yaml, preview.png?}, SUBMISSION.md, tasks.json,
                   tools/{sanity_check_submission.py, submission.py}, README.md (dataset card)
  STAGING/gt/      parts/<id>/{gt.step, gt_plan.json, key_dims.yaml, meta.yaml,
                   observability_mask.npz, registration_report.json}, README.md (card: PRIVATE)

Everything is copied from an ALLOWLIST: the inputs side takes only what recadbench-data already
exposes to agents (input.stl byte-identical, task.yaml); the GT side never takes gt_native*,
scan_raw.stl or anything else that is not listed above. tasks.json is built from task.yaml only.

Refuses a STAGING folder inside the public ReCADBench repo, inside any other ReCADBench checkout or
git work tree with a remote, inside --data-root / --gt-root, or a
non-empty STAGING/inputs|gt that this tool did not write. Default is NO push. With --push the GT
repo is always created private and the upload is refused unless the Hub reports it private; the
inputs repo is created private too unless --inputs-public is given (visibility of an existing repo
is never changed). Only the repo owner runs --push.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

import yaml

TOOL = Path(__file__).resolve()
REPO = TOOL.parents[2]  # the public ReCADBench checkout this tool lives in

MARKER = ".export_hf"  # written into every folder this tool owns (safe to rebuild)
INPUT_FILES = ("input.stl", "task.yaml")
GT_REQUIRED = ("gt.step", "meta.yaml")
GT_OPTIONAL = (
    "gt_plan.json",
    "key_dims.yaml",
    "observability_mask.npz",
    "registration_report.json",
)
GT_FILES = GT_REQUIRED + GT_OPTIONAL
# names that must never appear in either staging tree (checked again right before any upload)
FORBIDDEN_PREFIXES = ("gt_native", "scan_raw")
# GT file names that must never appear on the inputs side
GT_ONLY_NAMES = frozenset(GT_FILES) | {"gt_plan.json", "key_dims.yaml"}

SUBMISSION_DOC = Path("docs/benchmark/submission.md")
SANITY_TOOL = Path("tools/sanity_check_submission.py")
# stdlib-only module the sanity tool loads from next to itself when no evaluator is installed
SUBMISSION_MODULE = Path("evaluator/recadbench_eval/submission.py")
# a git work tree that contains one of these is a ReCADBench checkout (main clone or worktree)
RCB_CHECKOUT_MARKERS = ("evaluator/recadbench_eval", "dataset/tools/export_hf.py")


class ExportError(SystemExit):
    """Fatal, user-facing export problem."""


# ----------------------------------------------------------------------------- helpers


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _inside(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


def stl_triangles(path: Path) -> int | None:
    """Triangle count from the STL itself (binary header or ASCII facet count), stdlib only."""
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(84)
        if len(head) == 84:
            n = struct.unpack("<I", head[80:84])[0]
            if 84 + 50 * n == size:
                return int(n)
        f.seek(0)
        if not f.read(5).lower().startswith(b"solid"):
            return None
        f.seek(0)
        count = 0
        for line in f:
            if line.lstrip().startswith(b"facet"):
                count += 1
        return count


def _git_config(dotgit: Path) -> str:
    """Git config text behind a `.git` folder or a worktree `.git` file ("" if unreadable)."""
    try:
        if dotgit.is_file():
            line = dotgit.read_text(encoding="utf-8", errors="replace").strip()
            if not line.startswith("gitdir:"):
                return ""
            gitdir = Path(line[len("gitdir:") :].strip())
            if not gitdir.is_absolute():
                gitdir = dotgit.parent / gitdir
            common = gitdir / "commondir"
            if common.is_file():
                rel = Path(common.read_text(encoding="utf-8", errors="replace").strip())
                gitdir = rel if rel.is_absolute() else gitdir / rel
        else:
            gitdir = dotgit
        cfg = gitdir / "config"
        return cfg.read_text(encoding="utf-8", errors="replace") if cfg.is_file() else ""
    except OSError:
        return ""


def _enclosing_public_checkout(out: Path) -> Path | None:
    """First git work tree around `out` (or `out` itself) that is a ReCADBench checkout or has a
    remote configured, i.e. anything a later `git add -A` + push could publish GT from. A local-only
    repo without a remote (e.g. a home-folder repo) is not refused."""
    for d in (out, *out.parents):
        dotgit = d / ".git"
        if not (dotgit.is_dir() or dotgit.is_file()):
            continue
        if any((d / m).exists() for m in RCB_CHECKOUT_MARKERS):
            return d
        if "[remote " in _git_config(dotgit):
            return d
    return None


def _check_staging(out: Path, data_root: Path, gt_root: Path, repo: Path) -> None:
    if _inside(out, repo):
        raise ExportError(f"refusing to stage inside the public ReCADBench repo ({repo})")
    found = _enclosing_public_checkout(out)
    if found is not None:
        raise ExportError(
            "refusing to stage inside a git checkout with a remote or a ReCADBench"
            f" checkout ({found})"
        )
    for name, root in (("--data-root", data_root), ("--gt-root", gt_root)):
        if _inside(out, root) or _inside(root, out):
            raise ExportError(f"refusing: --out overlaps {name}")


def _fresh_dir(d: Path) -> None:
    """Create d empty. An existing d is only removed if this tool wrote it (marker file)."""
    if d.exists():
        if not d.is_dir():
            raise ExportError(f"refusing: {d} exists and is not a folder")
        if any(d.iterdir()):
            if not (d / MARKER).is_file():
                raise ExportError(f"refusing: {d} is not empty and was not written by export_hf")
            shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / MARKER).write_text("written by dataset/tools/export_hf.py\n", newline="\n")


def resolve_ids(data_root: Path, parts: list[str] | None, split: str | None) -> list[str]:
    if parts:
        ids = list(dict.fromkeys(parts))
    elif split:
        f = data_root / "splits" / f"{split}.txt"
        if not f.is_file():
            raise ExportError(f"split file not found: splits/{split}.txt")
        ids = [line.strip() for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        ids = sorted(d.name for d in (data_root / "parts").iterdir() if (d / "task.yaml").is_file())
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ExportError("no parts selected")
    for pid in ids:
        if not pid or "/" in pid or "\\" in pid or pid.startswith("."):
            raise ExportError(f"invalid part id {pid!r}")
    return ids


def _manifest(data_root: Path) -> dict:
    f = data_root / "MANIFEST.json"
    if not f.is_file():
        return {}
    try:
        files = json.loads(f.read_text(encoding="utf-8")).get("files", {})
    except (ValueError, AttributeError) as exc:
        raise ExportError("data MANIFEST.json is not a JSON object with a 'files' mapping") from exc
    if not isinstance(files, dict):
        raise ExportError("data MANIFEST.json 'files' is not a mapping")
    return files


def _find_preview(previews: Path | None, pid: str) -> Path | None:
    if previews is None:
        return None
    for cand in (previews / f"{pid}.png", previews / pid / "preview.png"):
        if cand.is_file():
            return cand
    return None


def task_row(pid: str, task: dict, stl: Path) -> dict:
    """Public tasks.json row: from task.yaml (agent-visible) + the STL itself, nothing from GT."""
    scan = task.get("scan") or {}
    if not isinstance(scan, dict):
        raise ExportError(f"{pid}: task.yaml 'scan' is not a mapping")
    tri = scan.get("triangles")
    if not isinstance(tri, int):
        tri = stl_triangles(stl)
    return {
        "id": pid,
        "tolerance_class": task.get("tolerance_class"),
        "tier": task.get("tier"),
        "triangles": tri,
        "known_defects": list(scan.get("known_defects") or []),
    }


# ----------------------------------------------------------------------------- cards


def inputs_card(rows: list[dict], inputs_repo: str | None) -> str:
    repo = inputs_repo or "<org>/recadbench-inputs"
    lines = [
        "---",
        "pretty_name: ReCADBench Open inputs",
        "license: other",
        "task_categories:",
        "- other",
        "tags:",
        "- cad",
        "- reverse-engineering",
        "- 3d-scan",
        "- benchmark",
        "size_categories:",
        "- n<1K",
        "---",
        "",
        "# ReCADBench Open: task inputs",
        "",
        "Raw 3D scans (scanner frame, byte-identical to the benchmark inputs) and their",
        "`task.yaml`. Reverse-engineer each scan into one clean, parametric solid, then submit a",
        "zip as described in `SUBMISSION.md`. Ground truth is kept private and is",
        "never part of this dataset.",
        "",
        "```",
        "parts/<id>/input.stl     raw scan, mm, scanner frame",
        "parts/<id>/task.yaml     agent-visible task metadata (class, tier, scan notes, budgets)",
        "parts/<id>/preview.png   optional preview of the scan",
        "tasks.json               [{id, tolerance_class, tier, triangles, known_defects}]",
        "SUBMISSION.md            submission package format",
        "tools/sanity_check_submission.py   check your zip before uploading",
        "tools/submission.py      stdlib-only zip checks used by the sanity tool",
        "```",
        "",
        f"Download: `huggingface-cli download {repo} --repo-type dataset --local-dir recadbench`",
        "",
        "| id | class | tier | triangles |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {r['tolerance_class']} | {r['tier']} | {r['triangles']} |")
    return "\n".join(lines) + "\n"


def gt_card(ids: list[str]) -> str:
    lines = [
        "---",
        "pretty_name: ReCADBench ground truth (PRIVATE)",
        "license: other",
        "---",
        "",
        "# ReCADBench ground truth: PRIVATE",
        "",
        "This dataset is private and must stay private. It is read only by the ReCADBench scoring",
        "job. Do not make it public, do not mirror it, do not copy any value from it into a public",
        "report, leaderboard row, issue or log.",
        "",
        "Per part: `gt.step`, `gt_plan.json`, `key_dims.yaml`, `meta.yaml`,",
        "`observability_mask.npz`, `registration_report.json` (when present). Native GT models and",
        "raw scans are not included.",
        "",
        f"Parts: {len(ids)}",
    ]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- build


def build_inputs(
    ids: list[str],
    data_root: Path,
    dst: Path,
    previews: Path | None,
    repo: Path,
    inputs_repo: str | None = None,
) -> list[dict]:
    doc, tool, module = repo / SUBMISSION_DOC, repo / SANITY_TOOL, repo / SUBMISSION_MODULE
    for f in (doc, tool, module):
        if not f.is_file():
            raise ExportError(f"missing in the repo: {f.relative_to(repo).as_posix()}")
    _fresh_dir(dst)
    manifest = _manifest(data_root)
    rows = []
    for pid in ids:
        src = data_root / "parts" / pid
        for name in INPUT_FILES:
            if not (src / name).is_file():
                raise ExportError(f"{pid}: {name} missing in --data-root")
        out = dst / "parts" / pid
        out.mkdir(parents=True)
        for name in INPUT_FILES:
            shutil.copyfile(src / name, out / name)
            entry = manifest.get(f"parts/{pid}/{name}")
            if entry is None:
                continue
            want = entry.get("sha256") if isinstance(entry, dict) else None
            if not isinstance(want, str):
                raise ExportError(f"data MANIFEST.json entry parts/{pid}/{name} has no sha256")
            if sha256(out / name) != want:
                raise ExportError(f"{pid}/{name}: sha256 does not match data MANIFEST.json")
        prev = _find_preview(previews, pid)
        if prev is not None:
            shutil.copyfile(prev, out / "preview.png")
        try:
            task = yaml.safe_load((out / "task.yaml").read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ExportError(f"{pid}: task.yaml is not valid YAML") from exc
        if not isinstance(task, dict):
            raise ExportError(f"{pid}: task.yaml is not a mapping")
        if task.get("part_id", pid) != pid:
            raise ExportError(f"{pid}: task.yaml part_id does not match the folder")
        rows.append(task_row(pid, task, out / "input.stl"))
    shutil.copyfile(doc, dst / "SUBMISSION.md")
    (dst / "tools").mkdir()
    shutil.copyfile(tool, dst / "tools" / SANITY_TOOL.name)
    shutil.copyfile(module, dst / "tools" / SUBMISSION_MODULE.name)
    (dst / "tasks.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (dst / "README.md").write_text(inputs_card(rows, inputs_repo), encoding="utf-8", newline="\n")
    return rows


def build_gt(ids: list[str], gt_root: Path, dst: Path) -> list[str]:
    _fresh_dir(dst)
    warnings = []
    for pid in ids:
        src = gt_root / pid
        for name in GT_REQUIRED:
            if not (src / name).is_file():
                raise ExportError(f"{pid}: {name} missing in --gt-root")
        out = dst / "parts" / pid
        out.mkdir(parents=True)
        for name in GT_FILES:
            if (src / name).is_file():
                shutil.copyfile(src / name, out / name)
            else:
                warnings.append(f"{pid}: no {name}")
    (dst / "README.md").write_text(gt_card(ids), encoding="utf-8", newline="\n")
    return warnings


def audit(inputs_dir: Path, gt_dir: Path) -> None:
    """Last line of defence before an upload: no forbidden file anywhere, no GT file in inputs."""
    for root in (inputs_dir, gt_dir):
        for f in root.rglob("*"):
            if f.name.lower().startswith(FORBIDDEN_PREFIXES):
                raise ExportError(f"forbidden file staged: {f.relative_to(root).as_posix()}")
    for f in inputs_dir.rglob("*"):
        if f.is_file() and (f.name in GT_ONLY_NAMES or f.suffix.lower() in (".step", ".stp")):
            raise ExportError(f"GT file staged on the inputs side: {f.relative_to(inputs_dir)}")


# ----------------------------------------------------------------------------- push


def push(
    inputs_dir: Path,
    gt_dir: Path,
    inputs_repo: str,
    gt_repo: str,
    inputs_public: bool = False,
    api=None,
) -> None:
    """Upload both folders. The GT repo is created private and verified private before upload."""
    if inputs_repo == gt_repo:
        raise ExportError("refusing: --inputs-repo and --gt-repo must differ")
    audit(inputs_dir, gt_dir)
    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()
    ignore = [MARKER]
    api.create_repo(gt_repo, repo_type="dataset", private=True, exist_ok=True)
    if getattr(api.repo_info(gt_repo, repo_type="dataset"), "private", None) is not True:
        raise ExportError(f"refusing: {gt_repo} is not private on the Hub")
    api.upload_folder(
        repo_id=gt_repo,
        repo_type="dataset",
        folder_path=str(gt_dir),
        ignore_patterns=ignore,
        commit_message="ReCADBench GT export",
    )
    api.create_repo(inputs_repo, repo_type="dataset", private=not inputs_public, exist_ok=True)
    api.upload_folder(
        repo_id=inputs_repo,
        repo_type="dataset",
        folder_path=str(inputs_dir),
        ignore_patterns=ignore,
        commit_message="ReCADBench inputs export",
    )


# ----------------------------------------------------------------------------- main


def main(argv: list[str] | None = None, *, repo: Path = REPO, api=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data-root", required=True, help="recadbench-data checkout")
    p.add_argument("--gt-root", required=True, help="recadbench-gt/parts")
    p.add_argument("--out", required=True, help="staging folder (outside the public repo)")
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--parts", nargs="+", metavar="ID", help="part ids")
    sel.add_argument("--split", help="split name in <data-root>/splits/<name>.txt")
    p.add_argument("--previews", help="folder with <id>.png or <id>/preview.png")
    p.add_argument("--push", action="store_true", help="upload to the Hub (owner only)")
    p.add_argument("--inputs-repo", help="HF dataset repo for the inputs")
    p.add_argument("--gt-repo", help="HF dataset repo for the GT (always private)")
    p.add_argument(
        "--inputs-public", action="store_true", help="create the inputs repo public (new repos)"
    )
    args = p.parse_args(argv)

    data_root, gt_root = Path(args.data_root).resolve(), Path(args.gt_root).resolve()
    out = Path(args.out).resolve()
    _check_staging(out, data_root, gt_root, repo)
    if out.exists() and not out.is_dir():
        raise ExportError(f"refusing: --out {out} exists and is not a folder")
    if args.push and not (args.inputs_repo and args.gt_repo):
        raise ExportError("--push needs --inputs-repo and --gt-repo")
    if not (data_root / "parts").is_dir():
        raise ExportError("--data-root has no parts/ folder")
    if not gt_root.is_dir():
        raise ExportError("--gt-root is not a folder")
    previews = Path(args.previews).resolve() if args.previews else None

    ids = resolve_ids(data_root, args.parts, args.split)
    rows = build_inputs(ids, data_root, out / "inputs", previews, repo, args.inputs_repo)
    warnings = build_gt(ids, gt_root, out / "gt")
    audit(out / "inputs", out / "gt")
    for w in warnings:
        print(f"warning: {w}")
    n_prev = sum((out / "inputs" / "parts" / r["id"] / "preview.png").is_file() for r in rows)
    print(f"staged {len(ids)} parts ({n_prev} previews) -> {out}")
    if args.push:
        push(out / "inputs", out / "gt", args.inputs_repo, args.gt_repo, args.inputs_public, api)
        print(f"pushed: {args.inputs_repo} (inputs), {args.gt_repo} (gt, private)")
    else:
        print("no push (dry run). Re-run with --push --inputs-repo R --gt-repo R (owner only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
