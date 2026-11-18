"""ReCADBench Open: the Hugging Face Space (Gradio Blocks + a small FastAPI report route).

Environment:
    RCB_MODE              local | hub (default local)
    RCB_ADMINS            comma-separated HF usernames that see the Admin tab
    RCB_TASKS_FILE        task ids, one per line (default: the 10 dev ids)
    RCB_REFERENCE_FILE    JSON list of reference rows (live-broadcast stations), optional
  local mode:
    RCB_LOCAL_ROOT        store + work folder (default ./space_data)
    RECADBENCH_GT_DIR     GT parts folder for the local evaluator subprocess
    RCB_INPUTS_DIR        local copy of the inputs dataset (tasks.json, parts/<id>/preview.png)
  hub mode:
    HF_TOKEN              Space secret: a FINE-GRAINED token with write on the submissions repo
                          and read on the GT repo only (never a personal full-access token);
                          the scoring job removes it from the scorer's environment
    RCB_SUBMISSIONS_REPO  dataset repo for zips, results.jsonl, reports
    RCB_GT_REPO           PRIVATE GT dataset, read only by the scoring job
    RCB_INPUTS_REPO       public inputs dataset (tasks.json, previews, downloads)
    RCB_EVAL_IMAGE        eval image built from space/Dockerfile.eval
    RCB_JOB_FLAVOR        default cpu-upgrade; RCB_JOB_NAMESPACE optional

Local smoke: ``RCB_MODE=local RCB_LOCAL_ROOT=<tmp> python space/app.py``.
"""

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gradio as gr  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response  # noqa: E402
from jobs import Dispatcher, HFJobsDispatcher, LocalDispatcher, refresh_jobs  # noqa: E402
from store import SUBMISSION_ID_RE, HubStore, LocalStore, Store  # noqa: E402
from submit import (  # noqa: E402
    admin_action,
    handle_submission,
    is_admin,
    load_task_ids,
    sweep_pending,
    username,
)

import leaderboard as lb  # noqa: E402

REPORT_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
REPORT_FILES = ("report.html", "report.json")

ABOUT_MD = """
## How ReCADBench Open scores a submission

Each task is a real 3D scan of a mechanical part. Your agent returns **one clean parametric solid**
(`output.step`, mm, scanner frame). The Open layer scores every upload automatically, out of **95**
(S5 editability is disabled):

| Part | Points | What it measures |
|---|---|---|
| **G0 gate** | pass/fail | One valid closed solid, not a faceted mesh, no freeform cover-up |
| **S1 deviation** | 40 | Share of surface inside the tight / wide band of the tolerance class |
| **S2-face parsimony** | 25 | Steps on `faces_pred / faces_gt` (full 0.35-1.5); >3 caps total |
| **S3 design intent** | 20 | Key dims in tolerance (ok / not ok only), standard values, relations |
| **S4 topology** | 10 | Through-hole count and face-type mix |

A gate failure scores 0 for that task. S1 counts only the region the scan saw. S2-face is a
provisional proxy for the feature-tree score.

**Open /95** is the mean over all 10 tasks; a missing task counts 0.

### Open vs Verified

* **Open** (this table): automatic, STEP only. A declared `features.json` or plan is shown,
  never scored. Rows start *unvalidated*.
* **Verified** (admin): if you include a `native/` model, an admin can rebuild it offline in a
  network-less sandbox; our adapter then extracts the real feature tree for the official G0 and S2
  (`N_pred / N_ref`) and PASS. Promoted rows get the *validated* badge.

Rows where a person edited geometry or steered the agent (`human_in_loop: true`) carry a badge.
Reports never show GT values: no GT render, no key-dimension nominals, no GT plan.
Submitted code is never executed by the Space or the Open scoring job.

Full rubric: `docs/architecture/03_SCORING.md`; design: ADR 0006.
"""

SUBMIT_MD = """
Upload one `submission.zip` (format: `SUBMISSION.md` in the inputs dataset; check it first with
`tools/sanity_check_submission.py`). Sign in with Hugging Face first. Limits: zip up to 300 MB,
each STEP up to 50 MB, 3 submissions per user per UTC day.
"""


@dataclass
class Config:
    mode: str = "local"
    task_ids: list[str] = field(default_factory=list)
    inputs_repo: str | None = None
    inputs_dir: Path | None = None
    tasks_json: Path | None = None
    reference_rows: list[dict] = field(default_factory=list)
    # Hugging Face OAuth only works inside a Space (or with a local HF login). Local mode uses a
    # fixed dev identity instead, so the smoke run needs no HF account.
    oauth: bool = False
    local_user: str = "local-user"


def build_backends(env: dict | None = None) -> tuple[Store, Dispatcher, Config]:
    env = dict(os.environ if env is None else env)
    mode = env.get("RCB_MODE", "local").strip().lower()
    cfg = Config(mode=mode, task_ids=load_task_ids(env.get("RCB_TASKS_FILE")))
    cfg.reference_rows = lb.load_reference_rows(env.get("RCB_REFERENCE_FILE"))
    cfg.oauth = mode == "hub" or bool(env.get("SPACE_ID"))
    cfg.local_user = env.get("RCB_LOCAL_USER") or "local-user"
    if mode == "hub":
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
            root / "work", gt_dir=env.get("RECADBENCH_GT_DIR"), tasks_file=env.get("RCB_TASKS_FILE")
        )
        if env.get("RCB_INPUTS_DIR"):
            cfg.inputs_dir = Path(env["RCB_INPUTS_DIR"])
            cfg.tasks_json = cfg.inputs_dir / "tasks.json"
        cfg.inputs_repo = env.get("RCB_INPUTS_REPO") or None
    else:
        raise SystemExit(f"RCB_MODE must be local or hub, got {mode!r}")
    return store, dispatcher, cfg


def _fetch_tasks_json(inputs_repo: str | None, token: str | None) -> Path | None:
    if not inputs_repo:
        return None
    try:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(inputs_repo, "tasks.json", repo_type="dataset", token=token))
    except Exception:  # noqa: BLE001 - Tasks tab falls back to bare ids
        return None


def _preview(cfg: Config, tid: str) -> str | None:
    if cfg.inputs_dir is not None:
        p = cfg.inputs_dir / "parts" / tid / "preview.png"
        return str(p) if p.is_file() else None
    if cfg.inputs_repo:
        return f"https://huggingface.co/datasets/{cfg.inputs_repo}/resolve/main/parts/{tid}/preview.png"
    return None


class _Refresher:
    """Polls running jobs at most every ``every_s`` seconds (called from UI events)."""

    def __init__(self, store: Store, dispatcher: Dispatcher, every_s: float = 20.0):
        self.store, self.dispatcher, self.every_s = store, dispatcher, every_s
        self._last = 0.0

    def __call__(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last >= self.every_s:
            self._last = now
            try:
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


def build_demo(store: Store, dispatcher: Dispatcher, cfg: Config) -> gr.Blocks:
    refresh = _Refresher(store, dispatcher)

    def all_rows() -> list[dict]:
        return list(cfg.reference_rows) + store.list_results()

    def leaderboard_value():
        refresh()
        headers, data = lb.table(all_rows())
        return gr.Dataframe(value=data, headers=headers)

    def heatmap_value() -> str:
        return lb.heatmap_html(all_rows(), cfg.task_ids)

    def my_rows(profile, force: bool = False) -> str:
        user = username(profile)
        if not user:
            return "Sign in to see your submissions."
        refresh(force=force)  # page load / submit: throttled poll; the button forces one
        mine = [r for r in store.list_results() if r.get("hf_user") == user]
        if not mine:
            return "No submissions yet."
        lines = ["| Submission | Status | Open /95 | Submitted | Report |", "|---|---|---|---|---|"]
        for r in reversed(mine):
            sid = r.get("submission_id", "")
            done = r.get("status") == "done"
            status = lb.STATUS_LABELS.get(r.get("status", ""), "")
            if r.get("status_message"):
                status += f" ({lb._md_text(r['status_message'])})"
            report = f"[report](reports/{sid}/report.html)" if done else ""
            total = lb._fmt(r.get("open_total")) if done else "-"
            lines.append(f"| {sid} | {status} | {total} | {r.get('submitted_at', '')} | {report} |")
        return "\n".join(lines)

    def on_submit(file, profile):
        path = file if isinstance(file, str) else getattr(file, "name", None)
        text = handle_submission(path, profile, store, dispatcher, task_ids=cfg.task_ids)
        return text, my_rows(profile)

    def admin_visible(profile):
        return gr.Tab(visible=is_admin(profile))

    def on_admin(action: str, sid: str, profile):
        msg = admin_action(action, (sid or "").strip(), profile, store, dispatcher)
        return msg, admin_table(profile)

    def admin_table(profile) -> str:
        if not is_admin(profile):
            return ""
        lines = [
            "| Submission | User | Status | Validated | Open /95 | Job |",
            "|---|---|---|---|---|---|",
        ]
        for r in reversed(store.list_results()):
            lines.append(
                f"| {r.get('submission_id', '')} | {lb._md_text(str(r.get('hf_user', '')))} | "
                f"{r.get('status', '')} | {'yes' if r.get('validated') else 'no'} | "
                f"{lb._fmt(r.get('open_total'))} | {lb._md_text(str(r.get('job_id', '')))} |"
            )
        return "\n".join(lines)

    headers, data = lb.table(all_rows())
    with gr.Blocks(title="ReCADBench Open") as demo:
        gr.Markdown(
            "# ReCADBench Open\nScan to parametric CAD: 10 real 3D scans, one clean solid each. "
            "Open /95 is scored automatically; *validated* rows were rebuilt by an admin."
        )
        with gr.Tab("Leaderboard"):
            board = gr.Dataframe(
                value=data,
                headers=headers,
                datatype=["str"] * (len(headers) - 1) + ["markdown"],
                interactive=False,
                wrap=True,
            )
            btn_refresh = gr.Button("Refresh", size="sm")
            gr.Markdown(
                "Status column: *validated* = rebuilt from the native model by an admin, "
                "*unvalidated* = automatic Open score only, *reference* = live-broadcast station."
            )
        with gr.Tab("Detailed"):
            gr.Markdown("Per-task Open total (0-95) for every scored submission.")
            heat = gr.HTML(heatmap_value())
        with gr.Tab("Tasks"):
            gr.Markdown(
                "The 10 public dev tasks. Each is a raw scan (`input.stl`, scanner frame, mm) plus "
                "`task.yaml`. No dimensions or ground truth are published."
            )
            for task in lb.load_tasks(cfg.tasks_json, cfg.task_ids):
                with gr.Row():
                    prev = _preview(cfg, task["id"])
                    if prev:
                        gr.Image(value=prev, show_label=False, height=180, scale=1)
                    gr.HTML(lb.task_card_html(task, cfg.inputs_repo), scale=3)
        with gr.Tab("Metrics / About"):
            gr.Markdown(ABOUT_MD)
        with gr.Tab("Submit"):
            gr.Markdown(SUBMIT_MD)
            if cfg.oauth:
                gr.LoginButton()
            else:
                gr.Markdown(
                    f"Local mode: submitting as `{cfg.local_user}` (no Hugging Face login)."
                )
            upload = gr.File(label="submission.zip", file_types=[".zip"], type="filepath")
            btn_submit = gr.Button("Submit", variant="primary")
            status = gr.Textbox(label="Status", lines=4, interactive=False)
            mine = gr.Markdown()
            btn_mine = gr.Button("Refresh my submissions", size="sm")
        with gr.Tab("Admin", visible=False) as admin_tab:
            adm_sid = gr.Textbox(label="Submission id (sub-xxxxxxxx)")
            adm_action = gr.Radio(
                ["promote", "demote", "rescore", "delete"], label="Action", value="promote"
            )
            btn_admin = gr.Button("Apply", variant="stop")
            adm_msg = gr.Textbox(label="Result", interactive=False)
            adm_rows = gr.Markdown()

        btn_refresh.click(leaderboard_value, outputs=board).then(heatmap_value, outputs=heat)
        btn_submit.click(_profiled(on_submit, cfg, 1), inputs=upload, outputs=[status, mine])
        btn_mine.click(_profiled(lambda p: my_rows(p, force=True), cfg, 0), outputs=mine)
        btn_admin.click(
            _profiled(on_admin, cfg, 2), inputs=[adm_action, adm_sid], outputs=[adm_msg, adm_rows]
        )
        demo.load(leaderboard_value, outputs=board)
        demo.load(heatmap_value, outputs=heat)
        demo.load(_profiled(my_rows, cfg, 0), outputs=mine)
        demo.load(_profiled(admin_visible, cfg, 0), outputs=admin_tab)
        demo.load(_profiled(admin_table, cfg, 0), outputs=adm_rows)
    return demo


def build_app(store: Store, dispatcher: Dispatcher, cfg: Config) -> FastAPI:
    """FastAPI app: public report pages under /reports, the Gradio UI at /."""
    app = FastAPI()

    def _headers() -> dict:
        return {"Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff"}

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

    demo = build_demo(store, dispatcher, cfg)
    return gr.mount_gradio_app(app, demo, path="/", max_file_size="300mb")


def main() -> None:
    store, dispatcher, cfg = build_backends()
    swept = sweep_pending(store, dispatcher)
    if swept:
        print(f"startup sweep: {len(swept)} stale submission(s) marked failed")
    app = build_app(store, dispatcher, cfg)
    import uvicorn

    host = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
    port = int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
