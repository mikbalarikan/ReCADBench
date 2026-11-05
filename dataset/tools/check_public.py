#!/usr/bin/env python3
"""Public-repo data guard (00_DECISIONS D3). Run in CI: `python dataset/tools/check_public.py`.

Fails (exit 1) if the repository contains:
  * CAD/mesh files (*.stl, *.step, *.stp, *.prt) outside the synthetic-fixture folders
    `backends/conformance/` and `evaluator/golden/`;
  * any ground-truth file (`gt_plan.json`, `key_dims.yaml`/`.yml`, `gt.step`, `gt_native.*`),
    anywhere — including inside the fixture folders.

Checks the files tracked by git (`git ls-files`); falls back to walking the tree outside a git
checkout. Optional arguments: explicit paths to check instead (e.g. from a pre-commit hook).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

CAD_EXTENSIONS = {".stl", ".step", ".stp", ".prt"}
SYNTHETIC_FIXTURE_DIRS = ("backends/conformance/", "evaluator/golden/")
GT_NAMES = {"gt_plan.json", "key_dims.yaml", "key_dims.yml", "gt.step", "gt.stp"}
GT_PREFIXES = ("gt_native.",)
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "results"}


def violation(path: str) -> str | None:
    """Return a reason if `path` (repo-relative, POSIX) must not be in the public repo."""
    p = PurePosixPath(path)
    name = p.name.lower()
    if name in GT_NAMES or name.startswith(GT_PREFIXES):
        return "ground-truth file (never allowed in the public repo)"
    if p.suffix.lower() in CAD_EXTENSIONS and not path.startswith(SYNTHETIC_FIXTURE_DIRS):
        return f"{p.suffix} file outside {' / '.join(SYNTHETIC_FIXTURE_DIRS)}"
    return None


def repo_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True,
        ).stdout
        return [f for f in out.decode().split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for f in root.rglob("*"):
            rel = f.relative_to(root)
            if f.is_file() and not SKIP_DIRS.intersection(rel.parts):
                files.append(rel.as_posix())
        return files


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[2]
    files = argv or repo_files(root)
    bad = [(f, r) for f in files if (r := violation(f))]
    for f, reason in bad:
        print(f"check_public: REJECT {f}: {reason}", file=sys.stderr)
    if bad:
        print(f"check_public: {len(bad)} forbidden file(s). Part data and GT belong in the "
              "private recadbench-data / recadbench-gt repos.", file=sys.stderr)
        return 1
    print(f"check_public: OK ({len(files)} files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
