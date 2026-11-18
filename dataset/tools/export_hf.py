#!/usr/bin/env python3
"""Stage the ReCADBench Open datasets for the Hugging Face Hub (inputs public-ready, GT private).

    python dataset/tools/export_hf.py --data-root <data> --gt-root <recadbench-gt>/parts \
        --out <STAGING> [--parts id ... | --split dev] [--previews DIR] \
        [--inputs-repo ORG/recadbench-inputs] [--space ORG/recadbench-open] \
        [--push --inputs-repo ORG/recadbench-inputs --gt-repo ORG/recadbench-gt [--inputs-public]]

Builds two local folders and, only with --push, uploads them:

  STAGING/inputs/  parts/<id>/{input.stl, task.yaml, preview.png?}, SUBMISSION.md, tasks.json,
                   tools/{sanity_check_submission.py, submission.py}, README.md (dataset card)
  STAGING/gt/      parts/<id>/{gt.step, gt_plan.json, key_dims.yaml, meta.yaml,
                   observability_mask.npz, registration_report.json}, README.md (card: PRIVATE)

Everything is copied from an ALLOWLIST: the inputs side takes only what recadbench-data already
exposes to agents (input.stl byte-identical, task.yaml); the GT side never takes gt_native*,
scan_raw.stl or anything else that is not listed above. tasks.json is built from task.yaml plus
the public task table docs/benchmark/tasks_public.json (no, title, family, difficulty), never GT.
Previews: <id>.png|.webp or <id>/preview.png|.webp are decoded and re-encoded as a pixel-only
preview.png (Pillow, max 768 px, no text/EXIF/ICC chunks, so no local path or host name can ride
along); without --previews, <repo>/docs/assets/tasks is used if present. SUBMISSION.md is
docs/benchmark/submission.md with its relative links turned into GitHub URLs.

Refuses a STAGING folder inside the public ReCADBench repo, inside any other ReCADBench checkout or
git work tree with a remote, inside --data-root / --gt-root, or a
non-empty STAGING/inputs|gt that this tool did not write. Default is NO push. With --push the GT
repo is always created private and the upload is refused unless the Hub reports it private; the
inputs repo is created private too unless --inputs-public is given (visibility of an existing repo
is never changed; the visibility the Hub reports is printed after the upload). --push also refuses
a SUBMISSION.md that still reads like a draft. Only the repo owner runs --push.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import posixpath
import re
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

CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
SUBMISSION_DOC = Path("docs/benchmark/submission.md")
SANITY_TOOL = Path("tools/sanity_check_submission.py")
# stdlib-only module the sanity tool loads from next to itself when no evaluator is installed
SUBMISSION_MODULE = Path("evaluator/recadbench_eval/submission.py")
# a git work tree that contains one of these is a ReCADBench checkout (main clone or worktree)
RCB_CHECKOUT_MARKERS = ("evaluator/recadbench_eval", "dataset/tools/export_hf.py")
# public task table (README "The 10 tasks"): only these keys are ever copied into tasks.json
TASKS_PUBLIC = Path("docs/benchmark/tasks_public.json")
PUBLIC_KEYS = ("no", "title", "family", "difficulty")
DIFFICULTIES = {"medium": "\U0001f7e2", "hard": "\U0001f7e0", "very hard": "\U0001f534"}
# renders of the RAW scans (already public in the README); used when --previews is omitted
DEFAULT_PREVIEWS = Path("docs/assets/tasks")
PREVIEW_PATTERNS = ("{pid}.png", "{pid}/preview.png", "{pid}.webp", "{pid}/preview.webp")
PREVIEW_MAX_PX = 768
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# chunks a staged preview.png may carry: pixels and colour basics only. Text (tEXt, iTXt, zTXt),
# eXIf, iCCP, tIME and private chunks can hold a local path, a host name or a scene file name.
PNG_CHUNKS_OK = frozenset(
    {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"sRGB", b"gAMA", b"cHRM", b"pHYs", b"sBIT"}
)
# relative Markdown link targets in submission.md (rewritten to GitHub URLs on the Hub)
REL_LINK_RE = re.compile(r"\]\((?![a-zA-Z][a-zA-Z0-9+.-]*:|#|/)([^)\s]+)\)")
# wording that must be gone from SUBMISSION.md before it goes public (--push refuses it)
DRAFT_RE = re.compile(
    r"\(draft\)|^>\s*draft\b|not open yet|limits \(planned\)|coming with the space",
    re.IGNORECASE | re.MULTILINE,
)
# files the inputs side may contain (anything else fails the audit)
INPUTS_TOP = ("README.md", "SUBMISSION.md", "tasks.json", MARKER)
INPUTS_TOOLS = (SANITY_TOOL.name, SUBMISSION_MODULE.name)
INPUTS_PART = INPUT_FILES + ("preview.png",)

DEFAULT_INPUTS_REPO = "mikbalarikan/recadbench-inputs"
DEFAULT_SPACE = "mikbalarikan/recadbench-open"
GITHUB_URL = "https://github.com/mikbalarikan/ReCADBench"
LIVE_URL = "https://youtube.com/live/S4zE6OxCbLg"
REPO_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")


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
    for pattern in PREVIEW_PATTERNS:
        cand = previews / pattern.format(pid=pid)
        if cand.is_file():
            return cand
    return None


def stage_preview(src: Path, dst: Path) -> None:
    """Re-encode a preview (png or webp) as a pixel-only PNG of at most 768 px.

    Never a byte copy: text chunks, EXIF, XMP and ICC data of the source (which can name a local
    path, a host or the scene file) are dropped by rebuilding the image from its pixels."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ExportError("staging previews needs Pillow (pip install pillow)") from exc
    try:
        with Image.open(src) as raw:
            img = ImageOps.exif_transpose(raw)  # keep the orientation the EXIF asked for
        alpha = "A" in img.getbands() or "transparency" in img.info
        img = img.convert("RGBA" if alpha else "RGB")
        img.thumbnail((PREVIEW_MAX_PX, PREVIEW_MAX_PX), Image.Resampling.LANCZOS)
        clean = Image.frombytes(img.mode, img.size, img.tobytes())  # pixels only, empty info
        clean.save(dst, format="PNG", optimize=True)
    except (OSError, ValueError) as exc:  # UnidentifiedImageError is an OSError
        raise ExportError(f"preview {src.name} could not be converted to PNG") from exc
    problem = png_problem(dst)
    if problem:  # cannot happen with a pixel-only image; checked so a Pillow change cannot leak
        raise ExportError(f"preview {src.name}: {problem}")


def png_problem(path: Path) -> str | None:
    """Why a staged preview.png is not a plain pixel-only PNG (None if it is). Stdlib only."""
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        return "not a PNG file"
    pos = len(PNG_SIGNATURE)
    while pos + 12 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        pos += 12 + size
        if pos > len(data):
            break
        if kind not in PNG_CHUNKS_OK:
            return f"carries a {kind.decode('latin-1', 'replace')!r} chunk (metadata)"
        if kind == b"IEND":
            return None if pos == len(data) else "has data after the IEND chunk"
    return "truncated PNG (no IEND chunk)"


def stage_submission_doc(src: Path, dst: Path) -> None:
    """Copy docs/benchmark/submission.md, turning its repo-relative links (../adr/...) into
    GitHub URLs: on the Hub they would resolve inside the dataset and return 404."""
    base = SUBMISSION_DOC.parent.as_posix()

    def absolute(m: re.Match) -> str:
        target = posixpath.normpath(posixpath.join(base, m.group(1)))
        if target.startswith("../"):
            return m.group(0)  # points outside the repo: leave it alone
        return f"]({GITHUB_URL}/blob/main/{target})"

    text = src.read_text(encoding="utf-8")
    dst.write_text(REL_LINK_RE.sub(absolute, text), encoding="utf-8", newline="\n")


def submission_doc_problems(path: Path) -> list[str]:
    """Draft / not-yet-open wording left in a staged SUBMISSION.md (empty list = ready)."""
    text = path.read_text(encoding="utf-8")
    found = list(dict.fromkeys(m.group(0).strip() for m in DRAFT_RE.finditer(text)))
    if REL_LINK_RE.search(text):
        found.append("relative link")
    return found


def load_tasks_public(path: Path) -> dict[str, dict]:
    """id -> {no, title, family, difficulty} from the public task table ({} if the file is absent).

    Only PUBLIC_KEYS are taken; any other key in the file is ignored."""
    if not path.is_file():
        return {}
    where = TASKS_PUBLIC.as_posix()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ExportError(f"{where} is not valid JSON") from exc
    if not isinstance(data, list):
        raise ExportError(f"{where} is not a list")
    out: dict[str, dict] = {}
    for entry in data:
        pid = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(pid, str) or not pid:
            raise ExportError(f"{where}: every entry needs a string 'id'")
        if pid in out:
            raise ExportError(f"{where}: duplicate id {pid!r}")
        info = {}
        no = entry.get("no")
        if no is not None:
            if not isinstance(no, int) or isinstance(no, bool) or no < 1:
                raise ExportError(f"{where}: {pid}: 'no' must be a positive integer")
            info["no"] = no
        for key in ("title", "family"):
            val = entry.get(key)
            if val is not None:
                if not isinstance(val, str) or not val.strip():
                    raise ExportError(f"{where}: {pid}: {key!r} must be a non-empty string")
                info[key] = val
        diff = entry.get("difficulty")
        if diff is not None:
            if diff not in DIFFICULTIES:
                allowed = ", ".join(DIFFICULTIES)
                raise ExportError(f"{where}: {pid}: difficulty must be one of: {allowed}")
            info["difficulty"] = diff
        out[pid] = info
    return out


def task_row(pid: str, task: dict, stl: Path, public: dict | None = None) -> dict:
    """Public tasks.json row: task.yaml (agent-visible) + the STL itself + the public task table
    (no, title, family, difficulty; omitted when the table has no entry), nothing from GT."""
    scan = task.get("scan") or {}
    if not isinstance(scan, dict):
        raise ExportError(f"{pid}: task.yaml 'scan' is not a mapping")
    tri = scan.get("triangles")
    if not isinstance(tri, int):
        tri = stl_triangles(stl)
    info = (public or {}).get(pid, {})
    return {
        "id": pid,
        **{k: info[k] for k in PUBLIC_KEYS if k in info},
        "tolerance_class": task.get("tolerance_class"),
        "tier": task.get("tier"),
        "triangles": tri,
        "known_defects": list(scan.get("known_defects") or []),
    }


def _sort_rows(rows: list[dict]) -> list[dict]:
    """Order by the public task number; rows without one keep their order, after the others."""
    return sorted(rows, key=lambda r: (r.get("no") is None, r.get("no") or 0))


def check_repo_id(repo_id: str, flag: str) -> str:
    if not REPO_ID_RE.fullmatch(repo_id or ""):
        raise ExportError(f"{flag}: invalid Hugging Face repo id {repo_id!r} (want owner/name)")
    return repo_id


# ----------------------------------------------------------------------------- cards


def _badge(
    label: str, message: str, color: str, alt: str, href: str | None = None, logo: str = ""
) -> str:
    def enc(s: str) -> str:
        return s.replace("-", "--").replace("_", "__").replace(" ", "%20").replace("/", "%2F")

    logo_q = f"&logo={logo}&logoColor=white" if logo else ""
    img = (
        f'<img src="https://img.shields.io/badge/{enc(label)}-{enc(message)}-{color}'
        f'?style=flat-square{logo_q}&labelColor=0D161F" alt="{html.escape(alt)}">'
    )
    return f'  <a href="{html.escape(href)}">{img}</a>' if href else f"  {img}"


def _esc(value) -> str:
    """Escape a value for an HTML cell or a Markdown table cell."""
    return html.escape(str(value)).replace("|", "&#124;")


def _nos(nums: list[int]) -> str:
    nums = sorted(nums)
    if len(nums) > 1 and nums == list(range(nums[0], nums[-1] + 1)):
        return f"{nums[0]:02d}\u2013{nums[-1]:02d}"
    return ", ".join(f"{n:02d}" for n in nums)


def _gallery(rows: list[dict], inputs_repo: str, previews: set[str]) -> list[str]:
    base = f"https://huggingface.co/datasets/{inputs_repo}/resolve/main/parts"
    lines = ["<table>"]
    for start in range(0, len(rows), 5):
        lines.append("  <tr>")
        for r in rows[start : start + 5]:
            pid = r["id"]
            title = r.get("title")
            name = f"{r['no']:02d} \u00b7 {title}" if title and "no" in r else (title or pid)
            cell = []
            if pid in previews:
                cell.append(
                    f'<img src="{base}/{_esc(pid)}/preview.png" width="120" '
                    f'alt="{_esc(title or pid)} scan">'
                )
            cell.append(f"<b>{_esc(name)}</b>")
            diff = r.get("difficulty")
            if diff:
                cell.append(f"<sub>{DIFFICULTIES[diff]} {_esc(diff)}</sub>")
            lines.append(f'    <td align="center" width="20%">{"<br>".join(cell)}</td>')
        lines.append("  </tr>")
    families: dict[str, list[int]] = {}
    for r in rows:
        if r.get("family") and "no" in r:
            families.setdefault(r["family"], []).append(r["no"])
    foot = [f"{_nos(nums)} \u00b7 {_esc(fam)}" for fam, nums in families.items()]
    foot.append("ground truth is never published")
    lines += [
        "  <tr>",
        f'    <td colspan="{min(5, len(rows))}" align="center"><sub>'
        + " &nbsp;|&nbsp; ".join(foot)
        + "</sub></td>",
        "  </tr>",
        "</table>",
    ]
    return lines


def _task_table(rows: list[dict]) -> list[str]:
    lines = [
        "| No | Task | id | Family | Difficulty | Class | Tier | Triangles |",
        "|:-:|---|---|---|---|:-:|:-:|--:|",
    ]
    for r in rows:
        no = f"{r['no']:02d}" if "no" in r else "\u2013"
        tri = r.get("triangles")
        cells = [
            no,
            _esc(r.get("title") or "\u2013"),
            f"`{_esc(r['id'])}`",
            _esc(r.get("family") or "\u2013"),
            _esc(r.get("difficulty") or "\u2013"),
            _esc(r.get("tolerance_class") if r.get("tolerance_class") is not None else "\u2013"),
            _esc(r.get("tier") if r.get("tier") is not None else "\u2013"),
            f"{tri:,}" if isinstance(tri, int) else "\u2013",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    defects = [r for r in rows if r.get("known_defects")]
    if defects:
        summary = "<summary>Known scan defects per task (from task.yaml)</summary>"
        lines += ["", "<details>", summary, ""]
        for r in defects:
            label = r.get("title") or r["id"]
            lines.append(f"**{_esc(label)}** (`{_esc(r['id'])}`)")
            lines.append("")
            lines += [f"- {_esc(' '.join(str(d).split()))}" for d in r["known_defects"]]
            lines.append("")
        lines.append("</details>")
    return lines


def inputs_card(
    rows: list[dict],
    inputs_repo: str | None = None,
    space: str | None = None,
    previews: set[str] | frozenset[str] = frozenset(),
) -> str:
    """Public dataset card (README.md) for the inputs repo. Never names a GT file or the GT repo."""
    repo = inputs_repo or DEFAULT_INPUTS_REPO
    space_url = f"https://huggingface.co/spaces/{space or DEFAULT_SPACE}"
    n = len(rows)
    lines = [
        "---",
        "pretty_name: ReCADBench Open inputs",
        "license: cc-by-4.0",
        "viewer: false",
        "language:",
        "- en",
        "task_categories:",
        "- other",
        "tags:",
        "- cad",
        "- reverse-engineering",
        "- 3d-scan",
        "- stl",
        "- step",
        "- parametric-cad",
        "- benchmark",
        "- agents",
        "size_categories:",
        "- n<1K",
        "---",
        "",
        "# ReCADBench Open: task inputs",
        "",
        "**Scan \u2192 parametric CAD reverse-engineering benchmark for AI agents.**",
        "",
        "<p>"
        + " ".join(
            b.strip()
            for b in (
                _badge(
                    "submit",
                    "ReCADBench Open Space",
                    "FF3347",
                    "Submit on the ReCADBench Open Space",
                    space_url,
                    "huggingface",
                ),
                _badge(
                    "GitHub", "ReCADBench", "ECF1F5", "ReCADBench on GitHub", GITHUB_URL, "github"
                ),
                _badge(
                    "live race", "YouTube", "FF0000", "Live race on YouTube", LIVE_URL, "youtube"
                ),
                _badge("tasks", f"{n} real scans", "FF9A4D", f"{n} real scans", "#tasks"),
                _badge("score", "Open /95", "9DFF2E", "Open score out of 95", space_url),
                _badge("license", "CC BY 4.0", "00E5FF", "CC BY 4.0", CC_BY_URL),
            )
        )
        + "</p>",
        "",
        "Each task is a **real 3D scan** of a mechanical part: `input.stl` is raw, noisy,",
        "holed and in the scanner frame, byte-identical to what the benchmark agents get.",
        "Your agent must rebuild it as **one clean, editable, parametric solid** that",
        "recovers the design intent: not a surface wrap, not a mesh-to-B-rep conversion, but",
        "a feature tree an engineer could open and edit. Five parts come from our own scans",
        "of an R385 12 V diaphragm pump, five from the open-source Open Dedica DIY espresso",
        "machine. Zip your STEP files, upload them on the",
        f"[ReCADBench Open Space]({space_url}) and get an automatic score out of 95 against",
        "hidden ground truth.",
        "",
        "## Tasks",
        "",
        "The images are renders of the raw scans: exactly what the agent gets, with every hole and",
        "artefact.",
        "",
        *_gallery(rows, repo, set(previews)),
        "",
        *_task_table(rows),
        "",
        "## Quickstart",
        "",
        "```bash",
        "pip install -U huggingface_hub",
        f"hf download {repo} --repo-type dataset --local-dir recadbench",
        "```",
        "",
        "```python",
        "import json",
        "from pathlib import Path",
        "",
        "from huggingface_hub import snapshot_download",
        "",
        f'root = Path(snapshot_download("{repo}", repo_type="dataset", local_dir="recadbench"))',
        'for task in json.loads((root / "tasks.json").read_text()):',
        '    scan = root / "parts" / task["id"] / "input.stl"  # raw scan, mm, scanner frame',
        '    print(task["id"], task.get("title"), task["tolerance_class"], scan.stat().st_size)',
        "```",
        "",
        "## Files",
        "",
        "```",
        "parts/<id>/input.stl     raw scan, mm, scanner frame (never repaired)",
        "parts/<id>/task.yaml     agent-visible task metadata: scanner, sessions, known scan",
        "                         defects, tolerance class, tier, budgets",
    ]
    if previews:
        lines.append("parts/<id>/preview.png   render of the raw scan")
    lines += [
        "tasks.json               [{id, no, title, family, difficulty, tolerance_class, tier,",
        "                           triangles, known_defects}]",
        "SUBMISSION.md            submission package format",
        "tools/sanity_check_submission.py   check your zip before uploading",
        "tools/submission.py      stdlib-only zip checks used by the sanity tool",
        "```",
        "",
        "## What to deliver",
        "",
        "Per task **one solid** as STEP, in **mm**, in the **scanner frame** (the same frame as",
        "`input.stl`; do not move or re-orient the part). It must be a real parametric body, not a",
        "faceted mesh conversion. One zip for all tasks:",
        "",
        "```",
        "submission.zip",
        "  meta.json                  submitter, agent, model, CAD backend, human_in_loop, ...",
        "  <id>/output.step           required: one solid, mm, scanner frame",
        "  <id>/native/...            optional: native CAD model (needed for Verified)",
        "  <id>/features.json         optional: declared feature list",
        "  <id>/plan/re_plan.v1.json  optional: reverse-engineering plan",
        "  <id>/run.json              optional: wall time, cost, tokens, turns",
        "```",
        "",
        "Full format and the `meta.json` keys: [`SUBMISSION.md`](SUBMISSION.md). Check the",
        "zip before you upload (`--inputs` points at the downloaded scans and turns on the",
        "size check against each scan):",
        "",
        "```bash",
        "pip install build123d   # optional: enables the STEP checks",
        "python recadbench/tools/sanity_check_submission.py submission.zip"
        " --inputs recadbench/parts",
        "```",
        "",
        "Without build123d only the zip layout and `meta.json` are checked. With it, every",
        "`output.step` must load as exactly one valid solid whose bounding-box diagonal is",
        "within 20 % of the scan's, which catches wrong units (inches, metres). It does not",
        "check position: keep the part in the scanner frame yourself.",
        "",
        "## Scoring (Open layer, automatic on every upload)",
        "",
        "| | Component | Points | What it rewards |",
        "|---|---|:-:|---|",
        "| **G0** | Gate | pass / fail | the STEP loads as exactly one valid, closed solid"
        " and is not a faceted mesh conversion; fail the gate and every score is 0 |",
        "| **S1** | Deviation | **40** | two-way surface distance to the hidden reference inside"
        " the class tolerance bands (observable region only) |",
        "| **S2-face** | Parsimony proxy | **25** | a face count close to the reference model;"
        " LEGO-style stacks of tiny features score low |",
        "| **S3** | Design intent | **20** | key dimensions within tolerance, standard values,"
        " coaxial and symmetric relations kept |",
        "| **S4** | Topology | **10** | matching through-hole count and face-type mix |",
        "| | **Open total** | **/95** | |",
        "",
        "Submissions with a native model can be **Verified** by an admin, which adds the official",
        "feature-tree S2 and the binary PASS. Full rubric:",
        f"[03_SCORING.md]({GITHUB_URL}/blob/main/docs/architecture/03_SCORING.md).",
        "",
        "## Rules",
        "",
        "- Ground truth is private. It lives only in a private dataset read by an isolated scoring",
        "  job and is never published; reports show key dimensions only as within / not within",
        "  tolerance.",
        "- The agent sees only `input.stl` and `task.yaml`.",
        "- No hand-editing: if a person edited geometry or steered the agent beyond the initial",
        '  prompt, set `"human_in_loop": true` in `meta.json`; those rows get a separate badge.',
        "- Do not use public CAD of the same commercial products; declare anything you used.",
        "- Uploaded code and native models are never executed; the automatic score reads the",
        "  STEP only. Partial submissions are fine (an unsolved task scores 0); 3 uploads per",
        "  user per day.",
        f"- These {n} tasks are a public dev set. A hidden test set follows later.",
        "",
        "## Submit",
        "",
        f"Sign in with Hugging Face on the [ReCADBench Open Space]({space_url}), upload",
        "`submission.zip` and get a per-task report with a deviation map on your model.",
        "",
        "## License and citation",
        "",
        "Scans, `task.yaml` files, previews and `tasks.json`:",
        "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). `tools/`: MIT, from the",
        f"[ReCADBench repository]({GITHUB_URL}).",
        "",
        "```bibtex",
        "@misc{recadbench2026,",
        "  title        = {ReCADBench: A Scan-to-Parametric-CAD Reverse-Engineering Benchmark"
        " for AI Agents},",
        "  year         = {2026},",
        "  howpublished = {\\url{" + GITHUB_URL + "}},",
        "  note         = {spec 0.3.0}",
        "}",
        "```",
    ]
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
    space: str | None = None,
) -> list[dict]:
    doc, tool, module = repo / SUBMISSION_DOC, repo / SANITY_TOOL, repo / SUBMISSION_MODULE
    for f in (doc, tool, module):
        if not f.is_file():
            raise ExportError(f"missing in the repo: {f.relative_to(repo).as_posix()}")
    public = load_tasks_public(repo / TASKS_PUBLIC)
    _fresh_dir(dst)
    manifest = _manifest(data_root)
    rows, with_preview = [], set()
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
            stage_preview(prev, out / "preview.png")
            with_preview.add(pid)
        try:
            task = yaml.safe_load((out / "task.yaml").read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ExportError(f"{pid}: task.yaml is not valid YAML") from exc
        if not isinstance(task, dict):
            raise ExportError(f"{pid}: task.yaml is not a mapping")
        if task.get("part_id", pid) != pid:
            raise ExportError(f"{pid}: task.yaml part_id does not match the folder")
        rows.append(task_row(pid, task, out / "input.stl", public))
    rows = _sort_rows(rows)
    stage_submission_doc(doc, dst / "SUBMISSION.md")
    (dst / "tools").mkdir()
    shutil.copyfile(tool, dst / "tools" / SANITY_TOOL.name)
    shutil.copyfile(module, dst / "tools" / SUBMISSION_MODULE.name)
    (dst / "tasks.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    card = inputs_card(rows, inputs_repo, space, with_preview)
    (dst / "README.md").write_text(card, encoding="utf-8", newline="\n")
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


def _allowed(rel: tuple[str, ...], top: tuple[str, ...], part: tuple[str, ...], tools=()) -> bool:
    if len(rel) == 1:
        return rel[0] in top
    if len(rel) == 2 and rel[0] == "tools":
        return rel[1] in tools
    return len(rel) == 3 and rel[0] == "parts" and rel[2] in part


def audit(inputs_dir: Path, gt_dir: Path) -> None:
    """Last line of defence before an upload: no forbidden file anywhere, no GT file in inputs,
    and every staged file is on its side's allowlist."""
    for root in (inputs_dir, gt_dir):
        for f in root.rglob("*"):
            if f.name.lower().startswith(FORBIDDEN_PREFIXES):
                raise ExportError(f"forbidden file staged: {f.relative_to(root).as_posix()}")
    for f in inputs_dir.rglob("*"):
        if f.is_file() and (f.name in GT_ONLY_NAMES or f.suffix.lower() in (".step", ".stp")):
            raise ExportError(f"GT file staged on the inputs side: {f.relative_to(inputs_dir)}")
    sides = (
        (inputs_dir, "inputs", INPUTS_TOP, INPUTS_PART, INPUTS_TOOLS),
        (gt_dir, "GT", ("README.md", MARKER), GT_FILES, ()),
    )
    for root, side, top, part, tools in sides:
        for f in root.rglob("*"):
            if f.is_file() and not _allowed(f.relative_to(root).parts, top, part, tools):
                rel = f.relative_to(root).as_posix()
                raise ExportError(f"file not on the {side} allowlist: {rel}")
    for f in sorted((inputs_dir / "parts").glob("*/preview.png")):
        problem = png_problem(f)
        if problem:
            rel = f.relative_to(inputs_dir).as_posix()
            raise ExportError(f"{rel} {problem}; previews must be pixel-only PNGs")


# ----------------------------------------------------------------------------- push


def push(
    inputs_dir: Path,
    gt_dir: Path,
    inputs_repo: str,
    gt_repo: str,
    inputs_public: bool = False,
    api=None,
) -> bool | None:
    """Upload both folders. The GT repo is created private and verified private before upload.

    Returns the inputs repo's `private` flag as the Hub reports it after the upload (None if the
    Hub does not say); an existing repo keeps its visibility, so this is printed, not assumed."""
    if inputs_repo == gt_repo:
        raise ExportError("refusing: --inputs-repo and --gt-repo must differ")
    audit(inputs_dir, gt_dir)
    draft = submission_doc_problems(inputs_dir / "SUBMISSION.md")
    if draft:
        raise ExportError(
            "refusing: SUBMISSION.md still reads like a draft (" + "; ".join(draft) + ")."
            f" Update {SUBMISSION_DOC.as_posix()} and stage again."
        )
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
    private = getattr(api.repo_info(inputs_repo, repo_type="dataset"), "private", None)
    state = _visibility(private)
    print(f"{inputs_repo} (inputs) is {state} on the Hub")
    if inputs_public and private is not False:
        print(
            f"warning: {inputs_repo} is still {state} although --inputs-public was given"
            " (an existing repo keeps its visibility). Make it public in the repo settings:"
            " the card gallery and the Space read its files anonymously."
        )
    return private


def _visibility(private: bool | None) -> str:
    return {True: "PRIVATE", False: "public"}.get(private, "of unknown visibility")


# ----------------------------------------------------------------------------- main


def main(argv: list[str] | None = None, *, repo: Path = REPO, api=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data-root", required=True, help="recadbench-data checkout")
    p.add_argument("--gt-root", required=True, help="recadbench-gt/parts")
    p.add_argument("--out", required=True, help="staging folder (outside the public repo)")
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--parts", nargs="+", metavar="ID", help="part ids")
    sel.add_argument("--split", help="split name in <data-root>/splits/<name>.txt")
    p.add_argument(
        "--previews",
        help="folder with <id>.png|.webp or <id>/preview.png|.webp"
        f" (default: <repo>/{DEFAULT_PREVIEWS.as_posix()} if present)",
    )
    p.add_argument("--push", action="store_true", help="upload to the Hub (owner only)")
    p.add_argument(
        "--inputs-repo",
        help=f"HF dataset repo for the inputs (card links default to {DEFAULT_INPUTS_REPO};"
        " --push needs it given explicitly)",
    )
    p.add_argument("--gt-repo", help="HF dataset repo for the GT (always private)")
    p.add_argument(
        "--space",
        default=DEFAULT_SPACE,
        help=f"submission Space linked from the card ({DEFAULT_SPACE})",
    )
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
    for flag, value in (("--inputs-repo", args.inputs_repo), ("--gt-repo", args.gt_repo)):
        if value is not None:
            check_repo_id(value, flag)
    check_repo_id(args.space, "--space")
    if not (data_root / "parts").is_dir():
        raise ExportError("--data-root has no parts/ folder")
    if not gt_root.is_dir():
        raise ExportError("--gt-root is not a folder")
    if args.previews:
        previews = Path(args.previews).resolve()
        if not previews.is_dir():
            raise ExportError("--previews is not a folder")
    else:
        previews = repo / DEFAULT_PREVIEWS if (repo / DEFAULT_PREVIEWS).is_dir() else None

    ids = resolve_ids(data_root, args.parts, args.split)
    rows = build_inputs(
        ids, data_root, out / "inputs", previews, repo, args.inputs_repo, args.space
    )
    warnings = build_gt(ids, gt_root, out / "gt")
    audit(out / "inputs", out / "gt")
    for w in warnings:
        print(f"warning: {w}")
    draft = submission_doc_problems(out / "inputs" / "SUBMISSION.md")
    if draft:
        print(
            f"warning: {SUBMISSION_DOC.as_posix()} still reads like a draft"
            f" ({'; '.join(draft)}); --push refuses it until it is updated"
        )
    n_prev = sum((out / "inputs" / "parts" / r["id"] / "preview.png").is_file() for r in rows)
    print(f"staged {len(ids)} parts ({n_prev} previews) -> {out}")
    if args.push:
        private = push(
            out / "inputs", out / "gt", args.inputs_repo, args.gt_repo, args.inputs_public, api
        )
        print(
            f"pushed: {args.inputs_repo} (inputs, {_visibility(private)}),"
            f" {args.gt_repo} (gt, private)"
        )
    else:
        print("no push (dry run). Re-run with --push --inputs-repo R --gt-repo R (owner only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
