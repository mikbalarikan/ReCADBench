"""ReCADBench Open: the Hugging Face Space (Gradio Blocks + a small FastAPI report route).

Environment:
    RCB_MODE              local | space | hub (default local)
    RCB_ADMINS            comma-separated HF usernames that see the Admin tab
    RCB_TASKS_FILE        task ids, one per line (default: the 10 dev ids)
    RCB_REFERENCE_FILE    JSON list of reference rows (live-broadcast stations), optional
  local mode:
    RCB_LOCAL_ROOT        store + work folder (default ./space_data)
    RECADBENCH_GT_DIR     GT parts folder for the local evaluator subprocess
    RCB_INPUTS_DIR        local copy of the inputs dataset (tasks.json, parts/<id>/preview.png)
  space mode (the deployed Space: scoring runs inside it, free cpu-basic; see runtime.py):
    HF_TOKEN              Space secret (fine-grained: write on submissions, read on GT); the
                          scorer subprocess is started without it and without OAUTH_* variables
    RCB_SUBMISSIONS_REPO  dataset repo for zips, results.jsonl, reports (persists restarts)
    RCB_GT_REPO           PRIVATE GT dataset, downloaded at startup to $HOME/.rcb-gt (outside
                          the app folder; never a static or allowed path, blocked for Gradio)
    RCB_INPUTS_REPO       public inputs dataset (tasks.json, previews, downloads)
    RCB_MAX_PARALLEL      scorer subprocesses at once (default 1); other uploads wait, FIFO
                          (also used in local mode); a poller thread advances the queue every
                          20 s; scorer work folder $HOME/.rcb-work (private, like the GT)
    RCB_TASK_TIMEOUT      seconds per scorer child (default 900); one open upload per user
  hub mode (scoring in HF Jobs on the eval image; kept for later, not deployed):
    HF_TOKEN              Space secret: a FINE-GRAINED token with write on the submissions repo
                          and read on the GT repo only (never a personal full-access token);
                          the scoring job removes it from the scorer's environment
    RCB_SUBMISSIONS_REPO  dataset repo for zips, results.jsonl, reports
    RCB_GT_REPO           PRIVATE GT dataset, read only by the scoring job
    RCB_INPUTS_REPO       public inputs dataset (tasks.json, previews, downloads)
    RCB_EVAL_IMAGE        eval image built from space/Dockerfile.eval
    RCB_JOB_FLAVOR        default cpu-upgrade; RCB_JOB_NAMESPACE optional

Static files: ``/assets`` serves ``space/assets/`` (deploy copies the banner/flow webps and
``tasks/*.webp`` there) or, in a repo checkout, ``docs/assets/``. Task titles fall back to
``tasks_public.json`` next to app.py or ``docs/benchmark/tasks_public.json``.

Local smoke: ``RCB_MODE=local RCB_LOCAL_ROOT=<tmp> python space/app.py``.
"""

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gradio as gr  # noqa: E402
import runtime  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from jobs import (  # noqa: E402
    Dispatcher,
    HFJobsDispatcher,
    LocalDispatcher,
    ScoringQueue,
    refresh_jobs,
)
from store import SUBMISSION_ID_RE, TASK_ID_RE, HubStore, LocalStore, Store  # noqa: E402
from submit import (  # noqa: E402
    MAX_PER_DAY,
    admin_action,
    handle_submission,
    is_admin,
    load_task_ids,
    sweep_pending,
    username,
)
from theme import CSS, HEAD, build_theme  # noqa: E402

import leaderboard as lb  # noqa: E402

REPORT_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
REPORT_FILES = ("report.html", "report.json")
# Gradio temp files (uploads, also anonymous ones): every 30 min, delete those older than 1 h
GRADIO_DELETE_CACHE = (1800, 3600)

GITHUB_URL = "https://github.com/mikbalarikan/ReCADBench"
LIVE_URL = "https://youtube.com/live/S4zE6OxCbLg"
DEFAULT_INPUTS_REPO = "mikbalarikan/recadbench-inputs"
SCORING_DOC_URL = f"{GITHUB_URL}/blob/main/docs/architecture/03_SCORING.md"
ADR_URL = f"{GITHUB_URL}/blob/main/docs/adr/0006-open-track.md"
# Optional launch banner without the README's "Open track · soon" chip; when it is not in the
# assets, header_html covers that chip on banner-dark.webp instead.
BANNER_LIVE = "banner-space-dark.webp"

# Submission constants: the evaluator module is the source of truth (stdlib only, copied next to
# app.py at deploy time); these fallbacks keep the page rendering if it is missing.
_FALLBACK_META_REQUIRED = (
    "submitter_name", "submission_name", "agent_url", "model", "cad_backend", "harness",
    "human_in_loop", "notes", "agree_to_publish",
)  # fmt: skip
_FALLBACK_LIMITS = {"zip_bytes": 300 * 2**20, "step_bytes": 50 * 2**20, "max_entries": 2000}
META_EXAMPLE = {
    "submitter_name": "Your name or team",
    "submission_name": "my-agent v1",
    "agent_url": "https://github.com/you/my-agent",
    "model": "claude-opus-5-5",
    "cad_backend": "build123d",
    "harness": "Claude Code + build123d-mcp",
    "human_in_loop": False,
    "notes": "One line about the approach.",
    "agree_to_publish": True,
}

# Event delegation for the rich HTML blocks: copy buttons on code blocks and links that switch
# to another tab (``data-tab`` = text the tab label contains).
HTML_JS = """
element.addEventListener('click', function (ev) {
  var c = ev.target.closest('[data-copy]');
  if (c) {
    var code = c.parentElement.querySelector('code');
    if (code && navigator.clipboard) {
      navigator.clipboard.writeText(code.innerText).then(function () {
        c.textContent = 'Copied';
        setTimeout(function () { c.textContent = 'Copy'; }, 1400);
      });
    }
    return;
  }
  var t = ev.target.closest('[data-tab]');
  if (t) {
    ev.preventDefault();
    var want = t.getAttribute('data-tab');
    // visible tab buttons first, then the overflow menu Gradio uses on narrow screens
    var all = Array.from(document.querySelectorAll('.rcb-tabs button'));
    var match = all.filter(function (x) {
      return x.textContent.indexOf(want) >= 0 && !x.closest('.visually-hidden');
    });
    var b = match.find(function (x) { return x.offsetParent !== null; }) || match[0];
    if (b) {
      b.click();
      var bar = document.querySelector('.rcb-tabs');
      if (bar) { bar.scrollIntoView({behavior: 'smooth', block: 'start'}); }
    }
  }
});
"""


def submission_constants() -> tuple[tuple[str, ...], dict]:
    """(META_REQUIRED, LIMITS) from recadbench_eval.submission, else the documented defaults."""
    try:
        from recadbench_eval.submission import LIMITS, META_REQUIRED

        return tuple(META_REQUIRED), dict(LIMITS)
    except Exception:  # noqa: BLE001 - the page must render without the evaluator module
        return _FALLBACK_META_REQUIRED, dict(_FALLBACK_LIMITS)


def resolve_assets_dir(here: Path = HERE) -> Path | None:
    """``space/assets`` when deployed, else ``<repo>/docs/assets`` (local dev), else None."""
    for cand in (here / "assets", here.parent / "docs" / "assets"):
        if cand.is_dir():
            return cand
    return None


def resolve_tasks_public(here: Path = HERE) -> Path | None:
    docs = here.parent / "docs" / "benchmark"
    for cand in (here / "tasks_public.json", docs / "tasks_public.json"):
        if cand.is_file():
            return cand
    return None


@dataclass
class Config:
    mode: str = "local"
    task_ids: list[str] = field(default_factory=list)
    inputs_repo: str | None = None
    inputs_dir: Path | None = None
    tasks_json: Path | None = None
    tasks_public: Path | None = None
    assets_dir: Path | None = None
    reference_rows: list[dict] = field(default_factory=list)
    # Hugging Face OAuth only works inside a Space (or with a local HF login). Local mode uses a
    # fixed dev identity instead, so the smoke run needs no HF account.
    oauth: bool = False
    local_user: str = "local-user"
    # in-app scoring (local / space mode): scorer children at once, and folders (GT, scorer
    # work) that Gradio must never serve
    max_parallel: int = 1
    private_dirs: list[Path] = field(default_factory=list)
    # space mode: retries the GT download from the poller while scoring is not configured
    gt_retry: runtime.GtRetry | None = None

    @property
    def public_inputs_repo(self) -> str:
        """Repo id shown in links and download instructions."""
        return self.inputs_repo or DEFAULT_INPUTS_REPO


def build_backends(
    env: dict | None = None, *, hub_api=None, gt_download=None
) -> tuple[Store, Dispatcher, Config]:
    """Store, dispatcher and page config for ``RCB_MODE``. ``hub_api`` / ``gt_download``
    replace the Hugging Face client and ``snapshot_download`` (tests, offline runs)."""
    env = dict(os.environ if env is None else env)
    mode = env.get("RCB_MODE", "local").strip().lower()
    cfg = Config(mode=mode, task_ids=load_task_ids(env.get("RCB_TASKS_FILE")))
    cfg.reference_rows = lb.load_reference_rows(env.get("RCB_REFERENCE_FILE"))
    cfg.oauth = mode == "hub" or bool(env.get("SPACE_ID"))
    cfg.local_user = env.get("RCB_LOCAL_USER") or "local-user"
    cfg.assets_dir = resolve_assets_dir()
    cfg.tasks_public = resolve_tasks_public()
    cfg.max_parallel = runtime.max_parallel(env)
    if mode == "space":
        setup = runtime.space_backends(env, app_dir=HERE, hub_api=hub_api, gt_download=gt_download)
        store, dispatcher = setup.store, setup.dispatcher
        cfg.private_dirs = list(setup.private_dirs)
        cfg.gt_retry = setup.gt_retry
        cfg.inputs_repo = env.get("RCB_INPUTS_REPO") or None
        cfg.tasks_json = _fetch_tasks_json(cfg.inputs_repo, env.get("HF_TOKEN") or None)
    elif mode == "hub":
        token = env.get("HF_TOKEN")
        repo = env["RCB_SUBMISSIONS_REPO"]
        store: Store = HubStore(repo, token)
        dispatcher: Dispatcher = HFJobsDispatcher(
            submissions_repo=repo,
            gt_repo=env["RCB_GT_REPO"],
            image=env["RCB_EVAL_IMAGE"],
            token=token,
            flavor=env.get("RCB_JOB_FLAVOR", "cpu-upgrade"),
            namespace=env.get("RCB_JOB_NAMESPACE") or None,
            task_ids=cfg.task_ids,  # the job scores exactly the task set uploads are checked on
        )
        cfg.inputs_repo = env.get("RCB_INPUTS_REPO") or None
        cfg.tasks_json = _fetch_tasks_json(cfg.inputs_repo, token)
    elif mode == "local":
        root = Path(env.get("RCB_LOCAL_ROOT") or "space_data").resolve()
        store = LocalStore(root / "store")
        dispatcher = LocalDispatcher(
            root / "work",
            gt_dir=env.get("RECADBENCH_GT_DIR"),
            tasks_file=env.get("RCB_TASKS_FILE"),
            timeout_s=runtime.task_timeout_s(env),
        )
        cfg.private_dirs = [root / "work"]
        if env.get("RECADBENCH_GT_DIR"):
            cfg.private_dirs.append(Path(env["RECADBENCH_GT_DIR"]))
        if env.get("RCB_INPUTS_DIR"):
            cfg.inputs_dir = Path(env["RCB_INPUTS_DIR"])
            cfg.tasks_json = cfg.inputs_dir / "tasks.json"
        cfg.inputs_repo = env.get("RCB_INPUTS_REPO") or None
    else:
        raise SystemExit(f"RCB_MODE must be local, space or hub, got {mode!r}")
    return store, dispatcher, cfg


def make_queue(store: Store, dispatcher: Dispatcher, cfg: Config) -> ScoringQueue | None:
    """The in-app FIFO scoring queue (local scorer only; HF Jobs queue on their side)."""
    if not isinstance(dispatcher, LocalDispatcher):
        return None
    return ScoringQueue(store, dispatcher, max_parallel=cfg.max_parallel)


def _fetch_tasks_json(inputs_repo: str | None, token: str | None) -> Path | None:
    if not inputs_repo:
        return None
    try:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(inputs_repo, "tasks.json", repo_type="dataset", token=token))
    except Exception:  # noqa: BLE001 - Tasks tab falls back to bare ids
        return None


def _preview_url(cfg: Config, tid: str) -> tuple[str | None, str | None]:
    """(preview URL, fallback URL) for a task card.

    Order: local inputs copy (``/previews/<id>.png``), the inputs dataset's
    ``parts/<id>/preview.png``, then the bundled scan render ``/assets/tasks/<id>.webp``
    (also the fallback if the first choice fails to load).
    """
    asset = None
    if cfg.assets_dir is not None and TASK_ID_RE.match(tid):
        if (cfg.assets_dir / "tasks" / f"{tid}.webp").is_file():
            asset = f"/assets/tasks/{tid}.webp"
    if cfg.inputs_dir is not None and (cfg.inputs_dir / "parts" / tid / "preview.png").is_file():
        return f"previews/{tid}.png", asset
    if cfg.inputs_repo:
        url = f"https://huggingface.co/datasets/{cfg.inputs_repo}/resolve/main/parts/{tid}/preview.png"
        return url, asset
    return asset, None


# --- static page content ----------------------------------------------------------------------

_e = lb.esc


def _chip(label: str, href: str, color: str) -> str:
    return (
        f"<a class='rcb-chip c-{color}' href='{_e(href)}' target='_blank' rel='noopener'>"
        f"<span class='dot'></span>{_e(label)}</a>"
    )


def header_html(cfg: Config) -> str:
    repo = cfg.public_inputs_repo
    chips = "".join(
        [
            _chip("GitHub", GITHUB_URL, "text"),
            _chip("Inputs dataset", f"https://huggingface.co/datasets/{repo}", "orange"),
            _chip("Live race", LIVE_URL, "red"),
            _chip("Submission format", f"https://huggingface.co/datasets/{repo}/blob/main/SUBMISSION.md",
                  "cyan"),
            _chip("MIT", f"{GITHUB_URL}/blob/main/LICENSE", "lime"),
        ]
    )  # fmt: skip
    alt = (
        "ReCADBench: a raw 3D scan goes in, a parametric STEP must come out. Ten 3D scans of "
        "pump and espresso-machine parts."
    )
    assets = cfg.assets_dir
    if assets is not None and (assets / BANNER_LIVE).is_file():
        banner = f"<img class='rcb-banner' src='/assets/{BANNER_LIVE}' alt='{alt}'>"
    elif assets is not None and (assets / "banner-dark.webp").is_file():
        # The README banner's last chip reads "Open track · soon"; on the launched site a
        # matching chip saying "live" is laid over it (positions in % of the 1600x560 art).
        banner = (
            f"<div class='rcb-banner-wrap'><img class='rcb-banner' src='/assets/banner-dark.webp' "
            f"alt='{alt}'><span class='rcb-banner-live' aria-hidden='true'><span>Open track "
            "&middot; <b>live</b></span></span></div>"
        )
    else:
        banner = (
            "<div style='padding:28px 22px 6px 22px;font:400 2.6rem/1 var(--rcb-display);"
            "letter-spacing:.02em'>ReCAD<span style='background:#00E5FF;color:#0D161F;"
            "padding:0 6px;margin-left:4px'>Bench</span> Open</div>"
        )
    return (
        f"<div class='rcb rcb-hero'>{banner}<div class='rcb-hero-body'>"
        "<p class='rcb-tagline'><b>ReCADBench Open</b> &middot; the scan &rarr; parametric CAD "
        "benchmark for AI agents. Rebuild 10 real 3D scans as clean solids, upload the STEP "
        "files, get an automatic score out of 95.</p>"
        f"<div class='rcb-chips'>{chips}</div></div></div>"
    )


def _code(text: str, label: str = "") -> str:
    # The Copy button sits in .box with the <pre> (not next to the label) so it is placed
    # relative to the code block itself; HTML_JS finds the code via the button's parent.
    lab = f"<div class='label'>{_e(label)}</div>" if label else ""
    return (
        f"<div class='rcb-code'>{lab}<div class='box'>"
        "<button class='copy' type='button' data-copy>Copy</button>"
        f"<pre><code>{_e(text)}</code></pre></div></div>"
    )


def meta_example_json(keys: tuple[str, ...]) -> str:
    return json.dumps({k: META_EXAMPLE.get(k, "...") for k in keys}, indent=2)


def zip_layout(task_ids: list[str]) -> str:
    first = task_ids[0] if task_ids else "<task_id>"
    last = task_ids[-1] if len(task_ids) > 1 else "<another_task_id>"
    tee, bend, bar = "\u251c\u2500", "\u2514\u2500", "\u2502"
    rows = [
        "submission.zip",
        f"{tee} meta.json               required",
        f"{tee} {first}/",
        f"{bar}  {tee} output.step          ONE solid, mm, scanner frame",
        f"{bar}  {tee} features.json        optional, declared features",
        f"{bar}  {tee} plan/re_plan.v1.json optional, RE plan",
        f"{bar}  {tee} native/              optional, for Verified",
        f"{bar}  {bend} run.json             optional, time / cost / tokens",
        f"{bend} {last}/",
        f"   {bend} output.step",
    ]
    return "\n".join(rows)


def _mb(n: int) -> str:
    return f"{n // 2**20} MB"


def take_part_html(cfg: Config) -> str:
    keys, _ = submission_constants()
    repo = cfg.public_inputs_repo
    n = len(cfg.task_ids)
    step1 = (
        "<div class='rcb-step' style='--accent:#FF9A4D'><div class='n'>01</div><div>"
        "<h3>Download the scans</h3>"
        f"<p>The public inputs dataset holds the {n} tasks: <code>parts/&lt;task_id&gt;/input.stl"
        "</code> (raw scan, scanner frame, mm, never repaired) and <code>task.yaml</code> "
        "(scanner, known defects, tolerance class, tier), plus the format spec and the checker.</p>"
        + _code(f"hf download {repo} --repo-type dataset --local-dir recadbench", "shell")
        + "</div></div>"
    )
    step2 = (
        "<div class='rcb-step' style='--accent:#00E5FF'><div class='n'>02</div><div>"
        "<h3>Run your agent</h3>"
        "<p>Any agent, any CAD backend: build123d, CadQuery, FreeCAD, Siemens NX, SolidWorks, "
        "Onshape, ... For each scan, deliver <b>one clean parametric solid</b>:</p><ul>"
        "<li><b>one solid</b> per task, valid and closed, a real B-rep (not a meshed or faceted "
        "conversion of the scan)</li>"
        "<li><b>millimetres</b>, in the <b>scanner frame</b> (the same frame as "
        "<code>input.stl</code>; do not re-centre or re-orient)</li>"
        "<li>the agent sees only <code>input.stl</code> and <code>task.yaml</code></li></ul>"
        "</div></div>"
    )
    step3 = (
        "<div class='rcb-step' style='--accent:#9DFF2E'><div class='n'>03</div><div>"
        "<h3>Package and check</h3>"
        "<p>One zip: <code>meta.json</code> at the top level plus one folder per task, named "
        "exactly by the task id. Missing tasks score 0. No other top-level entries, no symlinks, "
        "absolute paths or <code>..</code>.</p>"
        "<div class='rcb-two'>"
        + _code(zip_layout(cfg.task_ids), "zip layout")
        + _code(meta_example_json(keys), "meta.json")
        + "</div><p>Check the zip before you upload (run it inside <code>recadbench/</code>; "
        "add <code>--inputs parts</code> for the bounding-box check against the scans):</p>"
        + _code("python tools/sanity_check_submission.py submission.zip", "shell")
        + "</div></div>"
    )
    return f"<div class='rcb rcb-steps'>{step1}{step2}{step3}</div>"


def scores_in_jobs(cfg: Config) -> bool:
    """True only in hub mode (HF Jobs). The deployed Space (space mode) and local mode score in
    a subprocess next to the app, so the page must not promise a separate job."""
    return cfg.mode == "hub"


def _pace(cfg: Config) -> str:
    n = max(1, int(cfg.max_parallel))
    return "one at a time" if n == 1 else f"up to {n} at a time"


def step4_header_html(cfg: Config) -> str:
    who = (
        "Sign in with your Hugging Face account, then upload your zip."
        if cfg.oauth
        else f"Local mode: submitting as <code>{_e(cfg.local_user)}</code> (no Hugging Face login)."
    )
    if scores_in_jobs(cfg):
        when = (
            "Scoring runs in an isolated job and usually takes a few minutes; your row appears "
            "on the leaderboard when it is done."
        )
    else:
        where = "inside this Space" if cfg.mode == "space" else "next to this app"
        when = (
            f"Uploads are queued and scored {_pace(cfg)}, in order of arrival, by a separate "
            f"scorer process {where}, so a run can take a while when others are waiting. Your "
            "row appears on the leaderboard when it is done; <b>Refresh my submissions</b> shows "
            "its status."
        )
    return (
        "<div class='rcb'><div class='rcb-step4-head'><div class='n'>04</div><div>"
        f"<h3>Sign in and upload</h3><p class='muted' style='margin:0'>{who} {when}</p>"
        "</div></div></div>"
    )


def rules_html() -> str:
    _, limits = submission_constants()
    return (
        "<div class='rcb rcb-rules'>"
        "<div class='rcb-note' style='--accent:#00E5FF'><h4>Limits</h4><ul>"
        f"<li>zip up to <b>{_mb(int(limits.get('zip_bytes', 300 * 2**20)))}</b>, each STEP up to "
        f"<b>{_mb(int(limits.get('step_bytes', 50 * 2**20)))}</b>, at most "
        f"{int(limits.get('max_entries', 2000))} zip entries</li>"
        f"<li><b>{MAX_PER_DAY}</b> submissions per Hugging Face user per UTC day</li>"
        "<li>an identical zip (same sha256) is scored once; a failed run can be retried</li>"
        "</ul></div>"
        "<div class='rcb-note' style='--accent:#FF3347'><h4>Rules</h4><ul>"
        "<li>The agent sees only <code>input.stl</code> and <code>task.yaml</code>.</li>"
        "<li>Do not use public CAD of the same commercial products; declare anything you used.</li>"
        "<li>Set <code>human_in_loop: true</code> if a person edited geometry or steered the agent "
        "beyond the initial prompt (the row gets a badge).</li>"
        "<li>Partial submissions are fine; unsolved tasks score 0.</li>"
        "</ul></div></div>"
    )


SCORING_ROWS = (
    ("#00E5FF", "S1", "Deviation", 40,
     "Share of your surface inside the tight / wide tolerance band of the task's class, two-way "
     "against the hidden ground truth, on the region the scan actually saw."),
    ("#9DFF2E", "S2-face", "Parsimony", 25,
     "A step score on <code>faces_pred / faces_gt</code>: full marks for 0.35 to 1.5, fewer "
     "points further out; above 3 (LEGO-style stacks) the task total is capped. A provisional "
     "proxy for the feature-tree score."),
    ("#FF9A4D", "S3", "Design intent", 20,
     "Key dimensions within tolerance (reported only as ok / not ok), standard values, "
     "coaxial and symmetric relations kept."),
    ("#FF3347", "S4", "Topology", 10,
     "Matching through-hole count and face-type mix (planes, cylinders, cones, ...)."),
)  # fmt: skip


def scoring_html(cfg: Config) -> str:
    rows = "".join(
        f"<tr style='--accent:{c}'><td><div class='nm'>{name}<span>{key}</span></div></td>"
        f"<td class='pts'>{pts}<small>pt</small></td>"
        f"<td class='what'>{what}<div class='rcb-pbar'><span style='width:{pts / 40 * 100:.0f}%'>"
        "</span></div></td></tr>"
        for c, key, name, pts, what in SCORING_ROWS
    )
    flow = ""
    if cfg.assets_dir is not None and (cfg.assets_dir / "flow-dark.webp").is_file():
        # Class names must not contain ".dark": Gradio rewrites every CSS selector holding
        # ".dark" into a dark-mode-only rule (see theme.py), so "img.dark" breaks light mode.
        has_light = (cfg.assets_dir / "flow-light.webp").is_file()
        light = "<img class='flow-l' src='/assets/flow-light.webp' alt=''>" if has_light else ""
        flow = (
            f"<div class='rcb-flow{'' if has_light else ' only-d'}'><img class='flow-d' "
            "src='/assets/flow-dark.webp' alt='Pipeline: raw scan, your agent with any CAD "
            "kernel, parametric STEP, scored against hidden ground truth: S1 40, S2 25, S3 20, "
            f"S4 10.'>{light}</div>"
        )
    return (
        "<div class='rcb'>"
        "<div class='rcb-h'><span class='rcb-kicker'>Open layer</span><h2>How a submission is "
        "scored</h2></div>"
        "<p>Each task is a real 3D scan of a mechanical part. Your agent returns <b>one clean "
        "parametric solid</b> (<code>output.step</code>, mm, scanner frame). Every upload is "
        "scored automatically, out of <b>95</b> (S5 editability is not enabled yet).</p>"
        f"{flow}"
        "<div class='rcb-note' style='--accent:#FF3347'><b>G0 gate (pass / fail).</b> One valid "
        "closed solid, not a faceted mesh conversion, no freeform cover-up. A gate failure "
        "scores 0 for that task.</div>"
        f"<table class='rcb-points'><tbody>{rows}</tbody></table>"
        "<div class='rcb-note' style='--accent:#9DFF2E'><b>Open /95</b> = the mean over all "
        f"{len(cfg.task_ids)} tasks; a missing task counts 0. Reports show per-task scores and a "
        "deviation map on <i>your</i> model, never ground-truth values.</div>"
        "<div class='rcb-h' style='margin-top:22px'><span class='rcb-kicker'>Two layers</span>"
        "<h2>Open vs Verified</h2></div>"
        "<div class='rcb-tracks'>"
        "<div class='rcb-track' style='--accent:#00E5FF'><h3>Open</h3>"
        "<p class='muted' style='margin:0'>this leaderboard, every upload</p><ul>"
        "<li>automatic, STEP only</li>"
        "<li>a declared <code>features.json</code> or plan is shown, never scored</li>"
        "<li>rows start <span class='rcb-badge unvalidated'>unvalidated</span></li></ul></div>"
        "<div class='rcb-track' style='--accent:#9DFF2E'><h3>Verified</h3>"
        "<p class='muted' style='margin:0'>admin, when you include <code>native/</code></p><ul>"
        "<li>an admin rebuilds the native model offline in a network-less sandbox</li>"
        "<li>our adapter extracts the real feature tree for the official G0 and S2 "
        "(<code>N_pred / N_ref</code>) and PASS</li>"
        "<li>promoted rows get <span class='rcb-badge validated'>validated</span></li></ul></div>"
        "</div>"
        "<div class='rcb-legend'><span><span class='rcb-badge hil'>human in loop</span> a person "
        "edited geometry or steered the agent</span><span><span class='rcb-badge reference'>"
        "reference</span> live-broadcast station</span></div>"
        f"<p style='margin-top:14px'>Full rubric: <a href='{SCORING_DOC_URL}' target='_blank' "
        f"rel='noopener'>03_SCORING.md</a> &middot; design: <a href='{ADR_URL}' target='_blank' "
        "rel='noopener'>ADR 0006</a>.</p></div>"
    )


_GT_NO_VALUES = (
    "Reports never show GT values: no GT render, no key-dimension nominals, no GT plan. Key "
    "dimensions are reported only as within / not within tolerance."
)


def _faq_scoring(cfg: Config) -> tuple[tuple[str, str], ...]:
    """The two FAQ answers that depend on where scoring runs (hub = HF Jobs, else in-app)."""
    if scores_in_jobs(cfg):
        return (
            ("Is the ground truth public?",
             "<p>No. The ground-truth models live in a private dataset that only the scoring "
             f"job can read. {_GT_NO_VALUES}</p>"),
            ("Where does scoring run?",
             "<p>In an isolated Hugging Face Job on the evaluator image, not in this Space. The "
             "job drops its access token before the scorer starts, and only the public report "
             "and a summary row leave the job.</p>"),
        )  # fmt: skip
    space = cfg.mode == "space"
    here = "this Space" if space else "this app"
    cpu = " on a free CPU" if space else ""
    return (
        ("Is the ground truth public?",
         "<p>No. The ground-truth models live in a private dataset. "
         f"{'This Space' if space else 'This app'} keeps its copy in a private folder that the "
         "site never serves (not a static or downloadable path), and only the scorer reads it. "
         f"{_GT_NO_VALUES}</p>"),
        ("Where does scoring run?",
         f"<p>Inside {here}{cpu}, in a separate scorer process started for each upload. "
         f"Uploads are scored {_pace(cfg)}; the others wait in a first-in, first-out queue, so "
         "a run can take a while when others are waiting. The scorer process never receives "
         "the access token or the sign-in variables, and only the public report and your "
         "score row are published.</p>"),
    )  # fmt: skip


FAQ = (
    ("Do you run my code?",
     "<p>No. The site checks the zip structure without extracting or executing anything, and "
     "the Open scorer reads only <code>output.step</code>. A <code>native/</code> model is "
     "touched only for Verified, rebuilt by an admin offline in a network-less sandbox.</p>"),
    ("How often can I submit?",
     f"<p>{MAX_PER_DAY} submissions per Hugging Face user per UTC day. Uploading the same zip "
     "again is recognised by its sha256 and not scored twice; a failed run can be retried.</p>"),
    ("What does the human-in-loop badge mean?",
     "<p>Set <code>human_in_loop: true</code> in <code>meta.json</code> if a person edited "
     "geometry or steered the agent beyond the initial prompt. Those rows stay on the board "
     "with a <span class='rcb-badge hil'>human in loop</span> badge so fully autonomous runs "
     "can be compared fairly.</p>"),
    ("Why is my row unvalidated?",
     "<p>Every Open score is automatic. A row becomes <span class='rcb-badge validated'>"
     "validated</span> after an admin rebuilds your <code>native/</code> model and confirms "
     "the feature tree.</p>"),
    ("Is there a hidden test set?",
     "<p>These 10 tasks are a public dev set. A hidden test set follows later; dev-set scores "
     "stay on this board.</p>"),
    ("How do I get help or report a problem?",
     f"<p>Open an issue on <a href='{GITHUB_URL}/issues' target='_blank' rel='noopener'>"
     "GitHub</a>. Please do not post tokens or private data there.</p>"),
)  # fmt: skip


def faq_html(cfg: Config) -> str:
    items = "".join(
        f"<details><summary>{q}</summary><div class='a'>{a}</div></details>"
        for q, a in (*_faq_scoring(cfg), *FAQ)
    )
    return (
        "<div class='rcb'><div class='rcb-h'><span class='rcb-kicker'>FAQ</span>"
        f"<h2>Questions</h2></div><div class='rcb-faq'>{items}</div></div>"
    )


def footer_html() -> str:
    return (
        "<div class='rcb rcb-footer'>ReCADBench &middot; code "
        f"<a href='{GITHUB_URL}/blob/main/LICENSE' target='_blank' rel='noopener'>MIT</a>"
        f" &middot; <a href='{GITHUB_URL}' target='_blank' rel='noopener'>GitHub</a>"
        f" &middot; <a href='{LIVE_URL}' target='_blank' rel='noopener'>live race</a>"
        " &middot; ground truth is never published</div>"
    )


def _section(kicker: str, title: str, sub: str = "") -> str:
    s = f"<span class='sub'>{sub}</span>" if sub else ""
    return (
        f"<div class='rcb rcb-h'><span class='rcb-kicker'>{kicker}</span><h2>{title}</h2>{s}</div>"
    )


# --- app --------------------------------------------------------------------------------------


class _Refresher:
    """Polls running jobs at most every ``every_s`` seconds (called from UI events).

    With the in-app queue this is a non-blocking ``queue.tick``: when the poller thread is
    already ticking, the page does not wait for it. While a background poller runs
    (``queue.polled``, set by ``start_scoring``) it does nothing: page events never do Hub
    uploads or zip downloads in a Gradio worker that other visitors wait behind.
    """

    def __init__(
        self,
        store: Store,
        dispatcher: Dispatcher,
        every_s: float = 20.0,
        queue: ScoringQueue | None = None,
    ):
        self.store, self.dispatcher, self.every_s = store, dispatcher, every_s
        self.queue = queue
        self._last = 0.0

    def __call__(self, force: bool = False) -> None:
        if self.queue is not None and self.queue.polled:
            return
        now = time.monotonic()
        if force or now - self._last >= self.every_s:
            self._last = now
            try:
                if self.queue is not None:
                    self.queue.tick(blocking=False)
                else:
                    refresh_jobs(self.store, self.dispatcher)
            except Exception:  # noqa: BLE001 - never break the page on a poll error
                pass


def _profiled(fn, cfg: Config, n_inputs: int):
    """Gradio handler for ``fn(*inputs, profile)``.

    With OAuth the wrapper declares a ``gr.OAuthProfile | None`` parameter (Gradio injects it);
    without, it passes the fixed local dev identity.
    """
    if cfg.oauth:
        if n_inputs == 0:

            def w0(profile: gr.OAuthProfile | None):
                return fn(profile)

            return w0
        if n_inputs == 1:

            def w1(a, profile: gr.OAuthProfile | None):
                return fn(a, profile)

            return w1

        def w2(a, b, profile: gr.OAuthProfile | None):
            return fn(a, b, profile)

        return w2
    local = {"username": cfg.local_user}
    if n_inputs == 0:
        return lambda: fn(local)
    if n_inputs == 1:
        return lambda a: fn(a, local)
    return lambda a, b: fn(a, b, local)


def _html(value: str = "", js: bool = False, **kw) -> gr.HTML:
    """gr.HTML styled only by theme.CSS (no Gradio prose defaults) and without the default
    click->server event; ``js`` adds copy-button and tab-link handling."""
    classes = ["rcb-host", *kw.pop("elem_classes", [])]
    return gr.HTML(
        value,
        js_on_load=HTML_JS if js else None,
        apply_default_css=False,
        elem_classes=classes,
        **kw,
    )


def build_demo(
    store: Store, dispatcher: Dispatcher, cfg: Config, queue: ScoringQueue | None = None
) -> gr.Blocks:
    refresh = _Refresher(store, dispatcher, queue=queue)
    tasks = lb.order_tasks(lb.load_tasks(cfg.tasks_json, cfg.task_ids, cfg.tasks_public))
    task_order = [t["id"] for t in tasks]  # display order: task "no", then the task-id order

    def store_rows() -> list[dict]:
        try:
            return store.list_results()
        except Exception as e:  # noqa: BLE001 - Hub down: an empty board, never a page error
            runtime.log(f"could not read the results ({type(e).__name__})")
            return []

    def all_rows() -> list[dict]:
        return list(cfg.reference_rows) + store_rows()

    def board_views(force: bool = False):
        refresh(force=force)
        rows = all_rows()
        return (
            lb.leaderboard_html(rows, cfg.task_ids),
            lb.stats_html(lb.stats(rows, cfg.task_ids)),
            lb.heatmap_html(rows, task_order, tasks),
        )

    def my_rows(profile, force: bool = False) -> str:
        user = username(profile)
        if user:
            refresh(force=force)  # page load / submit: throttled poll; the button forces one
        return lb.my_submissions_html(store_rows(), user)

    def on_submit(file, profile):
        path = file if isinstance(file, str) else getattr(file, "name", None)
        text = handle_submission(
            path, profile, store, dispatcher, task_ids=cfg.task_ids, queue=queue
        )
        if not text.startswith("Error"):  # recorded: staged / stored, the temp copy is done
            drop_upload(path)
        return text, my_rows(profile)

    def admin_visible(profile):
        return gr.Tab(visible=is_admin(profile))

    def on_admin(action: str, sid: str, profile):
        msg = admin_action(action, (sid or "").strip(), profile, store, dispatcher, queue=queue)
        return msg, admin_table(profile)

    def admin_table(profile) -> str:
        if not is_admin(profile):
            return ""
        lines = [
            "| Submission | User | Status | Validated | Open /95 | Job |",
            "|---|---|---|---|---|---|",
        ]
        for r in reversed(store_rows()):
            lines.append(
                f"| {r.get('submission_id', '')} | {lb._md_text(str(r.get('hf_user', '')))} | "
                f"{r.get('status', '')} | {'yes' if r.get('validated') else 'no'} | "
                f"{lb._fmt(r.get('open_total'))} | {lb._md_text(str(r.get('job_id', '')))} |"
            )
        return "\n".join(lines)

    rows0 = all_rows()  # a Hub error at boot gives an empty board, never a crash
    previews = {t["id"]: _preview_url(cfg, t["id"]) for t in tasks}
    # anonymous POST /gradio_api/upload lands in Gradio's temp folder: drop old files
    with gr.Blocks(title="ReCADBench Open", delete_cache=GRADIO_DELETE_CACHE) as demo:
        _html(header_html(cfg))
        stats_box = _html(lb.stats_html(lb.stats(rows0, cfg.task_ids)))
        with gr.Tabs(elem_classes=["rcb-tabs"]):
            with gr.Tab("🏆 Leaderboard", id="leaderboard"):
                _html(_section("Open /95", "Leaderboard",
                               "automatic STEP-only scores, best first"))  # fmt: skip
                board = _html(lb.leaderboard_html(rows0, cfg.task_ids), js=True)
                with gr.Row():
                    btn_refresh = gr.Button("Refresh", size="sm", scale=0, min_width=120)
                _html(
                    "<div class='rcb rcb-legend'>"
                    "<span><span class='rcb-badge validated'>validated</span> rebuilt from the "
                    "native model by an admin</span>"
                    "<span><span class='rcb-badge unvalidated'>unvalidated</span> automatic "
                    "Open score only</span>"
                    "<span><span class='rcb-badge reference'>reference</span> live-broadcast "
                    "station</span>"
                    "<span><span class='rcb-badge hil'>human in loop</span> a person steered "
                    "or edited</span>"
                    "<span>S1-S4: mean over all tasks (missing = 0)</span></div>"
                )
            with gr.Tab("🧩 Tasks", id="tasks"):
                _html(_section(
                    f"{len(cfg.task_ids)} public dev tasks", "Tasks",
                    "renders of the raw scans: exactly what the agent gets, holes and all. "
                    "No dimensions or ground truth are published.",
                ))  # fmt: skip
                _html(lb.tasks_grid_html(tasks, cfg.public_inputs_repo, previews))
            with gr.Tab("🚀 Take part", id="take-part"):
                _html(_section("4 steps", "Take part",
                               "from download to leaderboard"))  # fmt: skip
                _html(take_part_html(cfg), js=True)
                with gr.Column(elem_classes=["rcb-step4"]):
                    _html(step4_header_html(cfg))
                    if cfg.oauth:
                        gr.LoginButton()
                    upload = gr.File(
                        label="submission.zip", file_types=[".zip"], type="filepath", height=150
                    )
                    btn_submit = gr.Button("Submit", variant="primary")
                    status = gr.Textbox(label="Status", lines=4, interactive=False)
                _html(_section("Yours", "My submissions"))
                mine = _html()
                with gr.Row():
                    btn_mine = gr.Button(
                        "Refresh my submissions", size="sm", scale=0, min_width=200
                    )
                _html(rules_html())
            with gr.Tab("📏 Scoring", id="scoring"):
                _html(scoring_html(cfg))
            with gr.Tab("🔎 Detailed", id="detailed"):
                _html(_section("Per task", "Detailed", "Open total (0-95) per task"))
                heat = _html(
                    lb.heatmap_html(rows0, task_order, tasks), elem_classes=["rcb-heat-host"]
                )
            with gr.Tab("❓ FAQ", id="faq"):
                _html(faq_html(cfg))
            with gr.Tab("Admin", id="admin", visible=False) as admin_tab:
                adm_sid = gr.Textbox(label="Submission id (sub-xxxxxxxx)")
                adm_action = gr.Radio(
                    ["promote", "demote", "rescore", "delete"], label="Action", value="promote"
                )
                btn_admin = gr.Button("Apply", variant="stop")
                adm_msg = gr.Textbox(label="Result", interactive=False)
                adm_rows = gr.Markdown()
        _html(footer_html())

        views = [board, stats_box, heat]
        btn_refresh.click(lambda: board_views(force=True), outputs=views)
        btn_submit.click(_profiled(on_submit, cfg, 1), inputs=upload, outputs=[status, mine])
        btn_mine.click(_profiled(lambda p: my_rows(p, force=True), cfg, 0), outputs=mine)
        btn_admin.click(
            _profiled(on_admin, cfg, 2), inputs=[adm_action, adm_sid], outputs=[adm_msg, adm_rows]
        )
        demo.load(board_views, outputs=views)
        demo.load(_profiled(my_rows, cfg, 0), outputs=mine)
        demo.load(_profiled(admin_visible, cfg, 0), outputs=admin_tab)
        demo.load(_profiled(admin_table, cfg, 0), outputs=adm_rows)
    return demo


def _gradio_build_assets() -> bool:
    return (Path(gr.__file__).resolve().parent / "templates" / "frontend" / "assets").is_dir()


def drop_upload(path: str | os.PathLike | None) -> bool:
    """Delete Gradio's temp copy of a handled upload (only a file inside Gradio's upload
    folder; the scorer reads its own staged copy, the store keeps the zip)."""
    if not path:
        return False
    try:
        from gradio.utils import get_upload_folder

        p = Path(path).resolve()
        if p.is_file() and p.is_relative_to(Path(get_upload_folder()).resolve()):
            p.unlink()
            return True
    except Exception:  # noqa: BLE001 - Gradio's delete_cache removes it later anyway
        pass
    return False


def ensure_space_oauth(cfg: Config, environ=None) -> bool:
    """Gradio installs MOCKED login routes (every visitor signs in as the HF account of the
    machine, i.e. the HF_TOKEN owner) unless ``SYSTEM=spaces``, which a Docker Space does not
    set by itself. Inside a Space (``SPACE_ID``) with sign-in enabled, force the real routes."""
    environ = os.environ if environ is None else environ
    if cfg.oauth and environ.get("SPACE_ID") and environ.get("SYSTEM") != "spaces":
        environ["SYSTEM"] = "spaces"
        runtime.log("SYSTEM=spaces was not set: using Hugging Face OAuth, never mocked sign-in")
        return True
    return False


def build_app(
    store: Store, dispatcher: Dispatcher, cfg: Config, queue: ScoringQueue | None = None
) -> FastAPI:
    """FastAPI app: brand assets under /assets, public report pages under /reports, Gradio at /.

    ``queue`` defaults to ``make_queue`` (in-app scoring); it is kept on ``app.state.rcb_queue``.
    GT and scorer work folders (``cfg.private_dirs``) are Gradio ``blocked_paths``: they are
    never static or allowed paths, and the block is a second guard on ``/gradio_api/file=``.
    """
    ensure_space_oauth(cfg)  # before any gr.Blocks: Gradio reads SYSTEM while building
    if queue is None:
        queue = make_queue(store, dispatcher, cfg)
    app = FastAPI()
    app.state.rcb_queue = queue

    def _headers() -> dict:
        return {"Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff"}

    if cfg.assets_dir is not None:
        # Gradio serves its own frontend build under /assets too and this mount shadows that
        # route, so the mount falls back to Gradio's build folder for every other file.
        packages = [("gradio", "templates/frontend/assets")] if _gradio_build_assets() else None
        app.mount(
            "/assets",
            StaticFiles(directory=str(cfg.assets_dir), packages=packages),
            name="rcb-assets",
        )

    @app.get("/previews/{name}")
    def preview_file(name: str):
        tid = name.removesuffix(".png")
        if cfg.inputs_dir is None or tid not in cfg.task_ids or not TASK_ID_RE.match(tid):
            return Response(status_code=404)
        p = cfg.inputs_dir / "parts" / tid / "preview.png"
        return FileResponse(p) if p.is_file() else Response(status_code=404)

    @app.get("/reports/{name}")
    def report_short(name: str):
        sid = name.removesuffix(".html")
        if not SUBMISSION_ID_RE.match(sid):
            return Response(status_code=404)
        return RedirectResponse(f"/reports/{sid}/report.html")

    @app.get("/reports/{sid}/{path:path}")
    def report_file(sid: str, path: str):
        if not SUBMISSION_ID_RE.match(sid):
            return Response(status_code=404)
        if path == "report.html":
            text = store.get_report_html(sid)
            if text is None:
                return Response("report not found", status_code=404)
            return HTMLResponse(text, headers=_headers())
        if path in REPORT_FILES or (path.startswith("renders/") and path.endswith(".png")):
            p = store.get_report_file(sid, path)
            if p is not None:
                return FileResponse(p, headers=_headers())
        return Response(status_code=404)

    demo = build_demo(store, dispatcher, cfg, queue=queue)
    return gr.mount_gradio_app(
        app,
        demo,
        path="/",
        max_file_size="300mb",
        theme=build_theme(),
        css=CSS,
        head=HEAD,
        ssr_mode=False,
        blocked_paths=[str(p) for p in cfg.private_dirs] or None,
    )


def start_scoring(
    store: Store, dispatcher: Dispatcher, cfg: Config, every_s: float = runtime.POLL_EVERY_S
) -> tuple[ScoringQueue | None, runtime.Poller | None]:
    """Startup: recover after a restart, sweep stale rows, start the background poller.

    In-app queue: runs whose scorer is gone (restart) are marked failed, rows still waiting are
    re-queued (never swept), and a daemon thread runs ``queue.tick`` every ``every_s`` seconds.
    A Hub error here never stops the site from starting: ``queue.tick`` retries the recovery
    (with the sweep) until it succeeds, before it dispatches anything.
    """
    queue = make_queue(store, dispatcher, cfg)
    if queue is None:
        try:
            swept = sweep_pending(store, dispatcher)
        except Exception as e:  # noqa: BLE001 - the site must start; never echo details
            runtime.log(f"startup sweep failed ({type(e).__name__})")
            swept = []
        if swept:
            print(f"startup sweep: {len(swept)} stale submission(s) marked failed")
        return None, None

    def sweep() -> None:
        swept = sweep_pending(store, dispatcher, keep_waiting=True)
        if swept:
            print(f"startup sweep: {len(swept)} stale submission(s) marked failed")

    queue.after_recover = sweep
    try:
        lost = queue.recover()
        if lost:
            print(f"startup: {len(lost)} interrupted run(s) marked failed")
    except Exception as e:  # noqa: BLE001 - retried by the poller
        runtime.log(f"startup recovery failed ({type(e).__name__}); the poller retries it")

    def tick() -> None:
        retry = cfg.gt_retry
        if retry is not None and isinstance(dispatcher, LocalDispatcher):
            try:
                retry(dispatcher)  # GT failed at boot: try again now and then
            except Exception as e:  # noqa: BLE001 - never stop the queue
                runtime.log(f"GT retry failed ({type(e).__name__})")
        queue.tick()

    queue.polled = True  # page events stop ticking: the poller is the only one doing queue work
    poller = runtime.Poller(tick, every_s=every_s).start()
    return queue, poller


def disable_scoring(dispatcher: Dispatcher, cfg: Config, why: str) -> None:
    """Never start a scorer (uploads stay pending, "scoring not configured"); logs ``why``."""
    runtime.log(f"scoring disabled: {why}")
    cfg.gt_retry = None
    if isinstance(dispatcher, LocalDispatcher):
        dispatcher.gt_dir = ""


def main() -> None:
    space = os.environ.get("RCB_MODE", "").strip().lower() == "space"
    hardened = runtime.harden_process() if space else False
    store, dispatcher, cfg = build_backends()
    if space:
        # the scorer (same uid) must not be able to read HF_TOKEN / OAUTH_CLIENT_SECRET
        why = runtime.isolation_problem(hardened)
        if why:
            disable_scoring(dispatcher, cfg, why)
    queue, _poller = start_scoring(store, dispatcher, cfg)
    app = build_app(store, dispatcher, cfg, queue=queue)
    import uvicorn

    host = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
    port = int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
