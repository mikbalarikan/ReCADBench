#!/usr/bin/env python3
"""Stage and (owner only) push ReCADBench Open to the Hugging Face Hub.

    python space/deploy_hf.py stage --out STAGING [--owner mikbalarikan] [--gradio-version X.Y.Z]
    python space/deploy_hf.py push --staging STAGING --what space|submissions|all-but-secrets
                                   [--dry-run]

stage builds two local folders from this checkout and audits them (nothing is uploaded):

  STAGING/space/             the PUBLIC app Space (sdk: docker, free cpu-basic). Scoring runs
                             inside it (RCB_MODE=space). Dockerfile (from space/docker/),
                             README.md (Space card), requirements.txt (every package pinned to the
                             version installed here, gradio to --gradio-version), every space/*.py
                             except deploy_hf.py and tests, tasks_public.json (public task titles,
                             key allowlist checked), assets/ (banner + flow webps and tasks/*.webp
                             from docs/assets: renders of the raw scans), spec/ and evaluator/
                             (pip-installed in the image; no tests, no caches; evaluator/golden
                             synthetic fixtures stay)
  STAGING/submissions_card/  README.md of the PRIVATE submissions dataset

The audit (run again before every push) refuses STL/STEP files outside evaluator/golden, GT file
names (gt.step, gt_plan.json, key_dims.yaml, meta.yaml, gt_native*, ...), anything that looks like
an HF token, absolute Windows user paths, tests, caches, deploy-only files and a top-level
recadbench_eval/ in the Space (it would shadow the evaluator installed in the image).

push uploads staged folders with the logged-in token (``hf auth login``). The submissions dataset
is created private and the upload is refused unless the Hub reports it private; the visibility of
an existing repo is never changed. The app Space is created public with sdk docker and gets Space
VARIABLES only: this tool never reads, prints or sets a token or a secret. Refuses a STAGING
folder inside the ReCADBench repo, inside any ReCADBench checkout or inside a git work tree with a
remote. (The private eval Space for HF Jobs is not deployed: no Pro account. space/Dockerfile.eval
and jobs.HFJobsDispatcher stay for a later switch, see hub_mode_variables.)
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Iterable
from importlib import metadata
from pathlib import Path

import yaml

TOOL = Path(__file__).resolve()
REPO = TOOL.parents[1]  # the ReCADBench checkout this tool lives in

MARKER = ".deploy_hf"  # written into every folder this tool owns (safe to rebuild, never uploaded)
DEFAULT_OWNER = "mikbalarikan"
APP_SPACE = "recadbench-open"
EVAL_SPACE = "recadbench-eval"  # future HF Jobs image (hub mode); not created by this tool
SUBMISSIONS_DATASET = "recadbench-submissions"
GT_DATASET = "recadbench-gt"
INPUTS_DATASET = "recadbench-inputs"
MAX_PARALLEL = "1"  # scorer subprocesses at once on the free cpu-basic Space (2 vCPU, 16 GB)
# Seconds per scorer child (score, then heatmap, per task). The slowest dev task (hs04) needs
# ~160 s on a fast desktop CPU; 420 s leaves headroom on cpu-basic. The evaluator default (900)
# would let one upload hold the single scorer slot for 5 h (10 tasks x 2 children).
TASK_TIMEOUT = "420"
# Whole-run watchdog (seconds): one upload never holds the scorer slot longer than 45 min.
MAX_RUN = "2700"
SPACE_ONLY_VARIABLES = frozenset({"RCB_MAX_PARALLEL", "RCB_TASK_TIMEOUT", "RCB_MAX_RUN"})
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?$")

# hub mode (scoring in HF Jobs on the eval image): NOT deployed, kept for the future switch.
JOB_FLAVOR = "cpu-basic"
# HF Job timeout (RCB_JOB_TIMEOUT). jobs.per_task_timeout_s splits it over 10 tasks x 2 scoring
# children after a 300 s reserve: 3h -> 525 s per child. The slowest dev task (hs04 pump head)
# needs ~160 s on a fast desktop CPU and runs over 75 s (the 30m split), so cpu-basic needs
# headroom. Jobs bill actual runtime: a higher cap costs nothing on normal runs.
JOB_TIMEOUT = "3h"
MIN_CHILD_TIMEOUT_S = 300  # tests: the hub-mode variables must leave at least this per child

# --what -> staged folder ("all-but-secrets" pushes both, submissions first)
FOLDERS = {"space": "space", "submissions": "submissions_card"}
ALL = "all-but-secrets"
PUSH_ORDER = ("submissions", "space")

# space/ top-level files that are runtime code of the app (every match is staged ...)
SPACE_GLOBS = ("*.py", "*.css", "*.js")
# ... except these (deploy-only / test files)
SPACE_SKIP = frozenset({"deploy_hf.py", "conftest.py"})
DOCKERFILE = Path("space/docker/Dockerfile")
DOCKERFILE_EVAL = Path("space/Dockerfile.eval")
EVAL_TREES = ("spec", "evaluator")  # pip-installed in the Space image
APP_CMD = 'CMD ["python", "app.py"]'
APP_PORT = 7860
# root-owned app folder (read-only for the uid 1000 user that also runs the scorer)
APP_WORKDIR = "WORKDIR /opt/rcb/app"
# The exec-form APP_CMD must be PID 1: the app makes itself non-dumpable before it starts a
# scorer. An ENTRYPOINT or an init wrapper as PID 1 would keep HF_TOKEN / OAUTH_CLIENT_SECRET in
# a /proc/1/environ that the scorer (same uid 1000) can read.
INIT_WRAPPER_RE = re.compile(r"\b(?:tini|dumb-init|docker-init|catatonit|s6-svscan|supervisord)\b")
# SYSTEM=spaces: without it Gradio installs MOCKED sign-in routes (everyone = the token owner)
APP_ENV = ("HOME=/home/user", "SYSTEM=spaces", "PYTHONNOUSERSITE=1",
           "GRADIO_SERVER_NAME=0.0.0.0", f"GRADIO_SERVER_PORT={APP_PORT}")  # fmt: skip
# modules the Dockerfile's build-time import check must cover
IMAGE_IMPORTS = ("recadbench_eval", "build123d", "OCP", "gradio")
# banner + flow images the UI shows (missing -> warning); banner-space-dark is optional
ASSET_FILES = ("banner-dark.webp", "flow-dark.webp", "flow-light.webp")
OPTIONAL_ASSET_FILES = ("banner-light.webp", "banner-space-dark.webp")
# public task titles the app reads next to app.py (no, title, family, difficulty; nothing else)
TASKS_PUBLIC = Path("docs/benchmark/tasks_public.json")
TASKS_PUBLIC_KEYS = frozenset({"id", "no", "title", "family", "difficulty"})
SKIP_DIRS = frozenset(
    {"tests", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", "build",
     "dist", "space_data"}
)  # fmt: skip
SKIP_SUFFIXES = (".pyc", ".pyo")

# audit rules
FORBIDDEN_NAMES = frozenset(
    {"gt.step", "gt_plan.json", "key_dims.yaml", "meta.yaml", "observability_mask.npz",
     "registration_report.json", "scan_raw.stl", "deploy_hf.py", "Dockerfile.eval",
     "results.jsonl"}
)  # fmt: skip
FORBIDDEN_PREFIXES = ("gt_native",)
CAD_SUFFIXES = (".stl", ".step", ".stp")
TOKEN_RE = re.compile(rb"hf_[A-Za-z0-9]{30,}")
WIN_USER_PATH_RE = re.compile(
    rb"(?i)(?:\b[a-z]:(?:\\\\|\\|/)+users(?:\\\\|\\|/)+[^\\/\s\"'<>]+|/(?:mnt/)?[a-z]/users/[^/\s\"'<>]+)"
)
REQUIRED = {
    "space": (
        "Dockerfile", "README.md", "requirements.txt", "app.py", "assets/banner-dark.webp",
        "spec/pyproject.toml", "evaluator/pyproject.toml", "evaluator/recadbench_eval/cli.py",
        "evaluator/recadbench_eval/submission.py",
    ),
    "submissions_card": ("README.md",),
}  # fmt: skip
# top-level names the app Space must not contain (shadow the installed packages from cwd)
SHADOW_DIRS = ("recadbench_eval", "recadbench_spec")

# Space card: values the contract fixes
APP_CARD = {
    "title": "ReCADBench Open",
    "emoji": "\U0001f4d0",
    "colorFrom": "red",
    "colorTo": "gray",
    "sdk": "docker",
    "app_port": APP_PORT,
    "pinned": True,
    "license": "mit",
    "hf_oauth": True,
    "hf_oauth_expiration_minutes": 480,
}
# gradio-sdk keys that a docker Space ignores (refused so the card does not mislead)
GRADIO_ONLY_KEYS = ("sdk_version", "app_file", "python_version")
SHORT_DESCRIPTION_MAX = 60
# modules the Space image provides (requirements.txt, their dependencies, spec + evaluator)
SPACE_DEPS = frozenset(
    {"gradio", "huggingface_hub", "filelock", "fastapi", "starlette", "uvicorn", "pydantic",
     "yaml", "PIL", "numpy", "pandas", "httpx", "jinja2", "orjson", "recadbench_eval",
     "recadbench_spec", "build123d", "OCP", "scipy", "matplotlib", "jsonschema"}
)  # fmt: skip

SUBMISSIONS_CARD = """\
---
pretty_name: ReCADBench Open submissions (PRIVATE)
license: other
tags:
  - recadbench
  - private
---

# ReCADBench Open submissions (PRIVATE)

**Keep this dataset private.** It holds the participants' uploads and the Space's bookkeeping. The
public face is the app Space [{{OWNER}}/{{APP_SPACE}}](https://huggingface.co/spaces/{{OWNER}}/{{APP_SPACE}}),
which shows only the public reports and leaderboard rows.

| Path | Written by | Content |
|---|---|---|
| `submissions/<sha256>.zip` | app Space | uploaded zips (STEP files, optional native models) |
| `results.jsonl` | app Space (sole writer) | one public row per submission + status fields |
| `reports/<submission_id>/` | app Space scorer | public report (`report.html`, `report.json`) |

No ground truth, GT-derived value or token is ever written here: the Space scores each upload in
a subprocess without the token and keeps the full `private/` result in its own work folder.
Write access: the app Space secret `HF_TOKEN` (fine-grained) and the owner.
"""

SECRET_STEPS = """\
NEXT (owner, by hand; this tool never sets secrets):
  1. https://huggingface.co/spaces/{app}/settings -> Variables and secrets -> New secret
     HF_TOKEN = a FINE-GRAINED token with write on datasets/{subs} and read on
     datasets/{gt}, nothing else (never a full-access token). The scorer subprocess never sees it.
  2. Restart the Space, sign in as {owner} and check that the Admin tab is visible. Without the
     secret the Space still starts; uploads stay pending ("scoring not configured").
"""


class DeployError(SystemExit):
    """Fatal, user-facing deploy problem."""


# ----------------------------------------------------------------------------- names


def check_owner(owner: str) -> str:
    if not OWNER_RE.match(owner or ""):
        raise DeployError(f"invalid --owner {owner!r}")
    return owner


def repo_ids(owner: str) -> dict[str, str]:
    check_owner(owner)
    return {
        "app": f"{owner}/{APP_SPACE}",
        "eval": f"{owner}/{EVAL_SPACE}",
        "submissions": f"{owner}/{SUBMISSIONS_DATASET}",
        "gt": f"{owner}/{GT_DATASET}",
        "inputs": f"{owner}/{INPUTS_DATASET}",
    }


def space_variables(owner: str) -> dict[str, str]:
    """Space VARIABLES of the app (public values; the HF_TOKEN secret is set by the owner)."""
    ids = repo_ids(owner)
    return {
        "RCB_MODE": "space",
        "RCB_SUBMISSIONS_REPO": ids["submissions"],
        "RCB_GT_REPO": ids["gt"],
        "RCB_INPUTS_REPO": ids["inputs"],
        "RCB_ADMINS": owner,
        "RCB_MAX_PARALLEL": MAX_PARALLEL,
        "RCB_TASK_TIMEOUT": TASK_TIMEOUT,
        "RCB_MAX_RUN": MAX_RUN,
    }


def hub_mode_variables(owner: str) -> dict[str, str]:
    """Variables of the future hub mode (HF Jobs on the eval image). Never set by push."""
    ids = repo_ids(owner)
    return {
        **{k: v for k, v in space_variables(owner).items() if k not in SPACE_ONLY_VARIABLES},
        "RCB_MODE": "hub",
        "RCB_EVAL_IMAGE": f"hf.co/spaces/{ids['eval']}",
        "RCB_JOB_FLAVOR": JOB_FLAVOR,
        "RCB_JOB_TIMEOUT": JOB_TIMEOUT,
        "RCB_JOB_NAMESPACE": owner,
    }


def render(text: str, owner: str) -> str:
    """Fill the {{OWNER}} / {{APP_SPACE}} placeholders of a template."""
    out = text.replace("{{OWNER}}", owner).replace("{{APP_SPACE}}", APP_SPACE)
    if "{{" in out:
        raise DeployError("unfilled placeholder in a template")
    return out


# ----------------------------------------------------------------------------- cards


_FRONT_RE = re.compile(r"\A---\n(.*?\n)---\n", re.S)


def split_front_matter(text: str) -> tuple[dict, str]:
    """(front matter as a dict, body) of a Hub card."""
    m = _FRONT_RE.match(text.replace("\r\n", "\n"))
    if not m:
        raise DeployError("card has no YAML front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise DeployError("card front matter is not a mapping")
    return meta, text.replace("\r\n", "\n")[m.end() :]


def check_app_card(text: str) -> list[str]:
    meta, body = split_front_matter(text)
    problems = [
        f"README front matter: {k} must be {v!r}, got {meta.get(k)!r}"
        for k, v in APP_CARD.items()
        if meta.get(k) != v or type(meta.get(k)) is not type(v)
    ]
    problems += [
        f"README front matter: {k} is a gradio-sdk key (docker Space: remove it)"
        for k in GRADIO_ONLY_KEYS
        if k in meta
    ]
    desc = meta.get("short_description")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > SHORT_DESCRIPTION_MAX:
        problems.append(
            f"README front matter: short_description must be 1..{SHORT_DESCRIPTION_MAX} chars"
        )
    tags = meta.get("tags")
    if not isinstance(tags, list) or not tags:
        problems.append("README front matter: tags must be a non-empty list")
    if not body.strip():
        problems.append("README has no body")
    return problems


def _req_name(line: str) -> str:
    return re.split(r"[\[<>=!~;\s#]", line.strip(), maxsplit=1)[0].lower().replace("_", "-")


def pin_requirements(requirements: str, versions: dict[str, str]) -> str:
    """requirements.txt with each listed package pinned to versions[name] (extras kept, comments
    dropped). Packages without a known version keep their line; gradio must be pinned."""
    versions = {k.lower().replace("_", "-"): v for k, v in versions.items()}
    out, pinned = [], set()
    for raw in requirements.replace("\r\n", "\n").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            if raw.strip().startswith("#"):
                out.append(raw.rstrip())
            continue
        name = _req_name(line)
        if name in versions:
            m = re.match(r"^([A-Za-z0-9._-]+(?:\[[^\]]*\])?)", line)
            line = f"{m.group(1)}=={versions[name]}"
            pinned.add(name)
        out.append(line)
    if "gradio" not in pinned:
        if "gradio" not in versions:
            raise DeployError("no gradio version to pin")
        out.append(f"gradio[oauth]=={versions['gradio']}")
    return "\n".join(out) + "\n"


def installed_versions(requirements: str) -> dict[str, str]:
    """name -> installed version for every package listed in requirements.txt (when installed)."""
    out = {}
    for raw in requirements.replace("\r\n", "\n").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = _req_name(line)
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return out


def installed_gradio_version() -> str:
    try:
        return metadata.version("gradio")
    except metadata.PackageNotFoundError as e:
        raise DeployError("gradio is not installed here; pass --gradio-version") from e


# ----------------------------------------------------------------------------- imports


def _imports(source: str, module_level_only: bool) -> set[str]:
    tree = ast.parse(source)
    if not module_level_only:
        nodes: Iterable[ast.AST] = ast.walk(tree)
    else:
        nodes, stack = [], list(tree.body)
        while stack:
            node = stack.pop()
            nodes.append(node)
            if isinstance(node, ast.If | ast.Try | ast.With):
                for name in ("body", "orelse", "finalbody", "handlers"):
                    stack.extend(getattr(node, name, []))
            elif isinstance(node, ast.ExceptHandler):
                stack.extend(node.body)
    mods: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def non_stdlib_imports(source: str, module_level_only: bool = True) -> set[str]:
    return {m for m in _imports(source, module_level_only) if m not in sys.stdlib_module_names}


def unknown_space_imports(sources: dict[str, str]) -> dict[str, set[str]]:
    """file -> imported modules that are neither stdlib, staged modules nor image deps."""
    local = {Path(n).stem for n in sources}
    out = {}
    for name, src in sources.items():
        extra = non_stdlib_imports(src, module_level_only=False) - local - SPACE_DEPS
        if extra:
            out[name] = extra
    return out


# ----------------------------------------------------------------------------- Dockerfiles


def docker_instructions(text: str) -> list[str]:
    """Instructions of a Dockerfile: comments dropped, continuations joined, whitespace folded."""
    out, cur = [], ""
    for raw in text.replace("\r\n", "\n").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            cur += line[:-1].strip() + " "
            continue
        out.append(" ".join((cur + line).split()))
        cur = ""
    if cur.strip():
        out.append(" ".join(cur.split()))
    return out


def dockerfile_drift(eval_text: str, space_text: str) -> list[str]:
    """OCP base of Dockerfile.eval (its instructions before the first WORKDIR: FROM, ENV, apt
    runtime libs) missing, in order, from the Space Dockerfile."""
    want = []
    for ins in docker_instructions(eval_text):
        if ins.upper().startswith("WORKDIR "):
            break
        want.append(ins)
    have = docker_instructions(space_text)
    missing, pos = [], 0
    for ins in want:
        try:
            pos = have.index(ins, pos) + 1
        except ValueError:
            missing.append(ins)
    return missing


def copy_sources(dockerfile: str) -> list[str]:
    """Source patterns of the COPY instructions (flags and destination dropped)."""
    out = []
    for ins in docker_instructions(dockerfile):
        parts = ins.split()
        if parts[0].upper() != "COPY" or any(a.startswith("--from") for a in parts[1:]):
            continue
        args = [a for a in parts[1:] if not a.startswith("--")]
        out += [a.rstrip("/") for a in args[:-1]]
    return out


def check_space_dockerfile(text: str, eval_text: str) -> list[str]:
    """Problems of the app Space Dockerfile (empty = ok)."""
    ins = docker_instructions(text)
    drift = dockerfile_drift(eval_text, text)
    problems = [f"drifted from {DOCKERFILE_EVAL.as_posix()}: {i}" for i in drift]
    if not ins or ins[-1] != APP_CMD:
        problems.append(f"last instruction must be {APP_CMD}")
    if not any(i.startswith("RUN useradd") and "-u 1000" in i for i in ins):
        problems.append("no uid 1000 user (useradd -u 1000)")
    users = [n for n, i in enumerate(ins) if re.fullmatch(r"USER (1000|user)", i)]
    if not users:
        problems.append("does not switch to the uid 1000 user")
    env_ins = [i for i in ins if i.startswith("ENV ")]
    env = set(" ".join(i[4:] for i in env_ins).split())
    for want in APP_ENV:
        if want not in env:
            problems.append(f"ENV {want} missing")
    if any(re.search(r"\bPATH=[^ ]*/home/", i) for i in env_ins):
        problems.append("PATH must not contain a folder under /home (writable by the scorer)")
    if APP_WORKDIR not in ins:
        problems.append(f"{APP_WORKDIR} missing")
    elif users and ins.index(APP_WORKDIR) > users[0]:
        problems.append(f"{APP_WORKDIR} must come before USER (a root-owned app folder)")
    if any(i.startswith("COPY ") and "--chown" in i for i in ins):
        problems.append("COPY --chown: app files must stay root-owned (read-only for the scorer)")
    if any(i.upper().startswith("ENTRYPOINT") for i in ins):
        problems.append(f"no ENTRYPOINT allowed: the exec-form {APP_CMD} must be PID 1")
    if any(i.upper().startswith(("CMD", "RUN")) and INIT_WRAPPER_RE.search(i) for i in ins):
        problems.append("no init wrapper (tini, dumb-init, ...): the app must be PID 1")
    if sum(1 for i in ins if i.upper().startswith("CMD")) != 1:
        problems.append(f"exactly one CMD allowed ({APP_CMD})")
    checks = [i for i in ins if i.startswith("RUN ") and "python -c" in i and "import" in i]
    for mod in IMAGE_IMPORTS:
        if not any(re.search(rf"\b{re.escape(mod)}\b", c) for c in checks):
            problems.append(f"build-time import check does not cover {mod}")
    if not any("pip install ./spec ./evaluator" in i for i in ins):
        problems.append("does not pip install ./spec ./evaluator")
    return problems


def uncopied_files(staged: Iterable[str], dockerfile: str) -> list[str]:
    """Staged top-level app files (code, json, css, js, assets/) that no COPY brings in."""
    sources = copy_sources(dockerfile)
    out = []
    for rel in staged:
        top = rel.split("/", 1)[0]
        if top in ("Dockerfile", "README.md", MARKER) or top in EVAL_TREES:
            continue
        if not any(fnmatch.fnmatchcase(top, s) for s in sources):
            out.append(rel)
    return sorted(out)


# ----------------------------------------------------------------------------- staging


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


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
                rel = Path(common.read_text(encoding="utf-8").strip())
                gitdir = rel if rel.is_absolute() else gitdir / rel
        else:
            gitdir = dotgit
        return (gitdir / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


RCB_CHECKOUT_MARKERS = ("evaluator/recadbench_eval", "space/deploy_hf.py")


def _enclosing_public_checkout(out: Path) -> Path | None:
    """First git work tree around `out` that is a ReCADBench checkout or has a remote."""
    for d in (out, *out.parents):
        dotgit = d / ".git"
        if not (dotgit.is_dir() or dotgit.is_file()):
            continue
        if any((d / m).exists() for m in RCB_CHECKOUT_MARKERS):
            return d
        if "[remote " in _git_config(dotgit):
            return d
    return None


def check_staging(out: Path, repo: Path) -> None:
    if _inside(out, repo) or _inside(repo, out):
        raise DeployError(f"refusing to stage inside (or around) the ReCADBench repo ({repo})")
    found = _enclosing_public_checkout(out)
    if found is not None:
        raise DeployError(
            "refusing to stage inside a git checkout with a remote or a ReCADBench checkout"
            f" ({found})"
        )


def _fresh_dir(d: Path, owner: str) -> None:
    """Create d empty. An existing d is only removed if this tool wrote it (marker file)."""
    if d.exists():
        if not d.is_dir():
            raise DeployError(f"refusing: {d} exists and is not a folder")
        if any(d.iterdir()):
            if not (d / MARKER).is_file():
                raise DeployError(f"refusing: {d} is not empty and was not written by deploy_hf")
            shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / MARKER).write_text(f"written by space/deploy_hf.py\nowner={owner}\n", newline="\n")


def marker_owner(folder: Path) -> str | None:
    try:
        text = (folder / MARKER).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^owner=(\S+)$", text, re.M)
    return m.group(1) if m else ""


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info")


def copy_tree(src: Path, dst: Path) -> int:
    """Copy src to dst without tests, caches, egg-info, dotfiles and bytecode. Returns files."""
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if not _skip_dir(d) and not d.startswith("."))
        for f in sorted(files):
            if f.startswith(".") or f.endswith(SKIP_SUFFIXES):
                continue
            s = Path(root, f)
            d = dst / s.relative_to(src)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            n += 1
    return n


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def space_files(space_dir: Path) -> list[Path]:
    """Runtime files of the app: every space/*.py (+ css/js) except deploy and test files."""
    files = {
        p
        for pattern in SPACE_GLOBS
        for p in space_dir.glob(pattern)
        if p.is_file()
        and p.name not in SPACE_SKIP
        and not p.name.startswith(("test_", "."))
        and not p.stem.endswith("_test")
    }
    return sorted(files)


def tasks_public_json(path: Path) -> str:
    """docs/benchmark/tasks_public.json re-serialised after an allowlist check of its keys."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DeployError(f"{TASKS_PUBLIC.as_posix()} is not readable JSON") from e
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise DeployError(f"{TASKS_PUBLIC.as_posix()} must be a list of objects")
    extra = sorted({k for r in rows for k in r} - TASKS_PUBLIC_KEYS)
    if extra:
        raise DeployError(f"{TASKS_PUBLIC.as_posix()} has keys outside the allowlist: {extra}")
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"


def build_app_space(dst: Path, repo: Path, owner: str, gradio_version: str) -> list[str]:
    """Stage the app Space (docker). Returns warnings."""
    space_dir, warnings = repo / "space", []
    dockerfile = (repo / DOCKERFILE).read_text(encoding="utf-8")
    problems = check_space_dockerfile(
        dockerfile, (repo / DOCKERFILE_EVAL).read_text(encoding="utf-8")
    )
    if problems:
        raise DeployError(f"{DOCKERFILE.as_posix()}:\n  " + "\n  ".join(problems))
    readme = (space_dir / "README.md").read_text(encoding="utf-8")
    if owner != DEFAULT_OWNER:
        readme = readme.replace(DEFAULT_OWNER, owner)
    problems = check_app_card(readme)
    if problems:
        raise DeployError("space/README.md:\n  " + "\n  ".join(problems))
    files = space_files(space_dir)
    if not any(p.name == "app.py" for p in files):
        raise DeployError("space/app.py is missing")

    _fresh_dir(dst, owner)
    _write(dst / "Dockerfile", dockerfile)
    _write(dst / "README.md", readme)
    req = (space_dir / "requirements.txt").read_text(encoding="utf-8")
    versions = {**installed_versions(req), "gradio": gradio_version}
    _write(dst / "requirements.txt", pin_requirements(req, versions))
    for p in files:
        shutil.copy2(p, dst / p.name)

    assets_src, assets = repo / "docs" / "assets", dst / "assets"
    for name in ASSET_FILES + OPTIONAL_ASSET_FILES:
        if (assets_src / name).is_file():
            assets.mkdir(exist_ok=True)
            shutil.copy2(assets_src / name, assets / name)
        elif name in ASSET_FILES:
            warnings.append(f"docs/assets/{name} is missing")
    tasks = sorted((assets_src / "tasks").glob("*.webp"))
    if not tasks:
        warnings.append("docs/assets/tasks/*.webp: no scan renders found")
    for p in tasks:
        (assets / "tasks").mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, assets / "tasks" / p.name)

    if (repo / TASKS_PUBLIC).is_file():
        _write(dst / TASKS_PUBLIC.name, tasks_public_json(repo / TASKS_PUBLIC))
    else:
        warnings.append(f"{TASKS_PUBLIC.as_posix()} is missing (task titles fall back to ids)")

    for tree in EVAL_TREES:
        if copy_tree(repo / tree, dst / tree) == 0:
            raise DeployError(f"{tree}/ is empty")

    staged = [p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file()]
    missing = uncopied_files(staged, dockerfile)
    if missing:
        raise DeployError(
            f"{DOCKERFILE.as_posix()} does not COPY these staged files: " + ", ".join(missing)
        )
    sources = {p.name: p.read_text(encoding="utf-8") for p in files if p.suffix == ".py"}
    for name, mods in sorted(unknown_space_imports(sources).items()):
        warnings.append(f"{name} imports {sorted(mods)}: not in requirements.txt / image deps")
    return warnings


def build_submissions_card(dst: Path, owner: str) -> None:
    _fresh_dir(dst, owner)
    _write(dst / "README.md", render(SUBMISSIONS_CARD, owner))


# ----------------------------------------------------------------------------- audit


def _in_golden(parts: tuple[str, ...]) -> bool:
    return any(a == "evaluator" and b == "golden" for a, b in zip(parts, parts[1:], strict=False))


def audit(staging: Path, folders: Iterable[str] = tuple(FOLDERS.values())) -> list[str]:
    """Problems that block an upload (empty = clean). Never echoes a matched token or path."""
    problems: list[str] = []
    for sub in folders:
        root = staging / sub
        if not root.is_dir():
            problems.append(f"{sub}/ is missing")
            continue
        problems += [f"{sub}/{r} is missing" for r in REQUIRED[sub] if not (root / r).is_file()]
        if sub == FOLDERS["space"]:
            problems += [
                f"{sub}/{d}/: would shadow the package installed in the image"
                for d in SHADOW_DIRS
                if (root / d).exists()
            ]
        for f in sorted(root.rglob("*")):
            rel = f.relative_to(root)
            shown = f"{sub}/{rel.as_posix()}"
            if f.is_dir():
                if _skip_dir(f.name):
                    problems.append(f"{shown}/: excluded folder staged")
                continue
            low = f.name.lower()
            if low.endswith(SKIP_SUFFIXES):
                problems.append(f"{shown}: bytecode staged")
            if f.name in FORBIDDEN_NAMES or low.startswith(FORBIDDEN_PREFIXES):
                problems.append(f"{shown}: forbidden file name")
            if f.suffix.lower() in CAD_SUFFIXES and not _in_golden(rel.parts):
                problems.append(f"{shown}: CAD/mesh file outside evaluator/golden")
            data = f.read_bytes()
            if TOKEN_RE.search(data):
                problems.append(f"{shown}: looks like it contains a Hugging Face token")
            if WIN_USER_PATH_RE.search(data):
                problems.append(f"{shown}: contains an absolute Windows user path")
    return problems


def tree_summary(folder: Path, collapse: Iterable[str] = ()) -> list[tuple[str, int, int]]:
    """(path, files, bytes) per file; folders named in `collapse` are summed into one row."""
    collapse, rows, agg = set(collapse), [], {}
    for f in sorted(folder.rglob("*")):
        if not f.is_file() or f.name == MARKER:
            continue
        rel = f.relative_to(folder)
        size = f.stat().st_size
        if len(rel.parts) > 1 and rel.parts[0] in collapse:
            n, b = agg.get(rel.parts[0], (0, 0))
            agg[rel.parts[0]] = (n + 1, b + size)
        else:
            rows.append((rel.as_posix(), 1, size))
    rows += [(f"{k}/", n, b) for k, (n, b) in agg.items()]
    return sorted(rows)


def stage(
    out: Path,
    owner: str = DEFAULT_OWNER,
    *,
    repo: Path = REPO,
    gradio_version: str | None = None,
) -> list[str]:
    """Build and audit STAGING/{space,submissions_card}. Returns warnings."""
    check_owner(owner)
    out = Path(out).resolve()
    check_staging(out, repo)
    if out.exists() and not out.is_dir():
        raise DeployError(f"refusing: --out {out} exists and is not a folder")
    gv = gradio_version or installed_gradio_version()
    warnings = build_app_space(out / FOLDERS["space"], repo, owner, gv)
    build_submissions_card(out / FOLDERS["submissions"], owner)
    problems = audit(out)
    if problems:
        raise DeployError("audit failed:\n  " + "\n  ".join(problems))
    return warnings


# ----------------------------------------------------------------------------- push


def _not_found(err: Exception) -> bool:
    return any(c.__name__ == "RepositoryNotFoundError" for c in type(err).__mro__)


def hub_private(api, repo_id: str, repo_type: str) -> bool | None:
    """True / False as the Hub reports it, None when the repo does not exist."""
    try:
        info = api.repo_info(repo_id, repo_type=repo_type)
    except Exception as e:
        if _not_found(e):
            return None
        raise
    return getattr(info, "private", None) is True


def _push_submissions(api, owner: str, folder: Path) -> None:
    repo_id = repo_ids(owner)["submissions"]
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
    if hub_private(api, repo_id, "dataset") is not True:
        raise DeployError(f"refusing: {repo_id} is not private on the Hub (not uploaded)")
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(folder),
        allow_patterns=["README.md"],
        ignore_patterns=[MARKER],
        commit_message="ReCADBench Open submissions card",
    )


def _push_app(api, owner: str, folder: Path, out: Callable[[str], None]) -> None:
    ids = repo_ids(owner)
    for key in ("submissions", "gt"):
        state = hub_private(api, ids[key], "dataset")
        if state is False:
            raise DeployError(f"refusing: {ids[key]} is PUBLIC on the Hub; it must be private")
        if state is None:
            out(f"warning: {ids[key]} does not exist yet (push it before the first submission)")
    variables = space_variables(owner)
    api.create_repo(
        ids["app"],
        repo_type="space",
        space_sdk="docker",
        private=False,
        exist_ok=True,
        space_variables=[{"key": k, "value": v} for k, v in variables.items()],
    )
    if hub_private(api, ids["app"], "space") is True:
        out(f"note: {ids['app']} is private on the Hub; make it public in its settings")
    for k, v in variables.items():
        api.add_space_variable(ids["app"], k, v)
    api.upload_folder(
        repo_id=ids["app"],
        repo_type="space",
        folder_path=str(folder),
        ignore_patterns=[MARKER],
        delete_patterns=["*.py"],  # stale modules only; staged files are never deleted
        commit_message="ReCADBench Open app (docker, in-Space scoring)",
    )


def _targets(owner: str) -> dict[str, str]:
    ids = repo_ids(owner)
    return {
        "space": f"space {ids['app']} (PUBLIC, docker) + variables",
        "submissions": f"dataset {ids['submissions']} (PRIVATE, card only)",
    }


def _checked_folder(staging: Path, what: str, owner: str | None) -> tuple[Path, str]:
    sub = FOLDERS[what]
    folder = Path(staging).resolve() / sub
    staged_owner = marker_owner(folder)
    if staged_owner is None:
        raise DeployError(f"{folder} was not written by deploy_hf stage")
    owner = check_owner(owner or staged_owner)
    if owner != staged_owner:
        raise DeployError(f"refusing: {sub}/ was staged for owner {staged_owner!r}, not {owner!r}")
    problems = audit(folder.parent, (sub,))
    if problems:
        raise DeployError(
            "audit failed:\n  " + "\n  ".join(problems) + "\nre-run `deploy_hf.py stage` to"
            " rebuild the folder (smoke-test a copy, or use `python -B`, never the staged folder)"
        )
    return folder, owner


def push(
    staging: Path,
    what: str,
    owner: str | None = None,
    *,
    api=None,
    dry_run: bool = False,
    out: Callable[[str], None] = print,
) -> None:
    if what != ALL and what not in FOLDERS:
        raise DeployError(f"--what must be one of {sorted([*FOLDERS, ALL])}")
    whats = PUSH_ORDER if what == ALL else (what,)
    # every folder is checked (owner, audit) before anything is uploaded
    checked = [(w, *_checked_folder(staging, w, owner)) for w in whats]
    for w, folder, own in checked:
        n = sum(1 for f in folder.rglob("*") if f.is_file() and f.name != MARKER)
        out(f"push {FOLDERS[w]}/ ({n} files) -> {_targets(own)[w]}")
        if w == "space":
            for k, v in space_variables(own).items():
                out(f"  variable {k}={v}")
    if dry_run:
        out("dry run: nothing uploaded")
        return
    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()
    try:
        user = api.whoami()["name"]
    except Exception as e:  # noqa: BLE001 - not logged in / network
        raise DeployError("not logged in to Hugging Face (run `hf auth login`)") from e
    out(f"logged in as {user}")
    for w, folder, own in checked:
        if w == "submissions":
            _push_submissions(api, own, folder)
        else:
            _push_app(api, own, folder, out)
            ids = repo_ids(own)
            out(SECRET_STEPS.format(app=ids["app"], subs=ids["submissions"], gt=ids["gt"],
                                    owner=own))  # fmt: skip
        out(f"pushed {FOLDERS[w]}/ -> {_targets(own)[w]}")


# ----------------------------------------------------------------------------- main


def _print_summary(out_dir: Path, out: Callable[[str], None]) -> None:
    for sub in FOLDERS.values():
        rows = tree_summary(out_dir / sub, collapse=(*EVAL_TREES, "assets"))
        total = sum(b for _, _, b in rows)
        out(f"{sub}/  ({sum(n for _, n, _ in rows)} files, {total / 1024:.1f} KiB)")
        for path, n, b in rows:
            extra = f"  [{n} files]" if n > 1 else ""
            out(f"  {path:<48} {b / 1024:9.1f} KiB{extra}")


def main(argv: list[str] | None = None, *, repo: Path = REPO, api=None, out=print) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("stage", help="build + audit the staging folders (no upload)")
    s.add_argument("--out", required=True, help="staging folder (outside any ReCADBench checkout)")
    s.add_argument("--owner", default=DEFAULT_OWNER, help="Hub user / org (default %(default)s)")
    s.add_argument("--gradio-version", help="gradio pin (default: the installed gradio)")
    u = sp.add_parser("push", help="upload staged folders (owner only, logged in)")
    u.add_argument("--staging", required=True)
    u.add_argument("--what", required=True, choices=[*sorted(FOLDERS), ALL])
    u.add_argument("--owner", help="default: the owner the folder was staged for")
    u.add_argument("--dry-run", action="store_true", help="show the plan, upload nothing")
    args = p.parse_args(argv)

    if args.cmd == "stage":
        out_dir = Path(args.out).resolve()
        warnings = stage(out_dir, args.owner, repo=repo, gradio_version=args.gradio_version)
        for w in warnings:
            out(f"warning: {w}")
        _print_summary(out_dir, out)
        out(f"audit: clean -> {out_dir}")
        out("variables `push --what space` will set:")
        for k, v in space_variables(args.owner).items():
            out(f"  {k}={v}")
        out(f"no upload. Next (owner): push --what {ALL} (submissions, then space).")
    else:
        push(Path(args.staging), args.what, args.owner, api=api, dry_run=args.dry_run, out=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
