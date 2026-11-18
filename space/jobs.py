"""Scoring dispatch for the ReCADBench Open Space.

A ``Dispatcher`` starts one scoring run per submission and reports its state:

* ``LocalDispatcher``: ``python -m recadbench_eval.cli score-submission`` as a local subprocess,
  GT from ``RECADBENCH_GT_DIR`` (local smoke runs / a home evaluator host).
* ``HFJobsDispatcher``: a Hugging Face Job (``huggingface_hub.run_job``) on the eval image
  (``space/Dockerfile.eval``). The job downloads the zip and the private GT dataset, runs
  ``score-submission`` and uploads ONLY ``public/`` (to ``reports/<id>/``) and ``summary.json``
  (to ``jobs/<id>/summary.json``) into the submissions repo. ``private/`` never leaves the job.

The Space stays the single writer of ``results.jsonl``: ``refresh_jobs`` polls running rows and
merges finished summaries through the store (row allowlist applies).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from store import SHA256_RE, SUBMISSION_ID_RE, TASK_ID_RE, Store

# job states returned by Dispatcher.status
QUEUED, RUNNING, COMPLETED, ERROR, UNKNOWN = "queued", "running", "completed", "error", "unknown"

# Fields a finished summary may set on the row. Identity/meta fields come from the zip at upload
# time and the Space bookkeeping fields are owned by the Space, so neither is taken from a job.
SUMMARY_FIELDS = (
    "eval_version",
    "layer",
    "open_total",
    "n_solved",
    "n_gate_ok",
    "per_task",
    "declared",
)


def row_fields_from_summary(summary: dict) -> dict:
    return {k: summary[k] for k in SUMMARY_FIELDS if k in summary}


class Dispatcher(ABC):
    @abstractmethod
    def dispatch(self, submission_id: str, sha: str, zip_ref: str) -> str:
        """Start scoring; return a job id. Raises on failure to start."""

    @abstractmethod
    def status(self, job_id: str) -> str:
        """One of queued | running | completed | error | unknown."""

    @abstractmethod
    def collect(self, submission_id: str, job_id: str, store: Store) -> dict | None:
        """After ``completed``: publish the public report via ``store`` and return the summary."""


# --- local ------------------------------------------------------------------------------------


class LocalDispatcher(Dispatcher):
    def __init__(
        self,
        work_root: str | os.PathLike,
        gt_dir: str | None = None,
        python: str = sys.executable,
        tasks_file: str | None = None,
        layer: str = "open",
        timeout_s: int = 900,
        popen: Any = subprocess.Popen,
    ):
        self.work_root = Path(work_root)
        self.gt_dir = gt_dir if gt_dir is not None else os.environ.get("RECADBENCH_GT_DIR")
        self.python = python
        self.tasks_file = tasks_file
        self.layer = layer
        self.timeout_s = timeout_s
        self._popen = popen
        self._procs: dict[str, Any] = {}

    def out_dir(self, submission_id: str) -> Path:
        return self.work_root / submission_id

    def command(self, zip_path: str, out_dir: Path, submission_id: str) -> list[str]:
        cmd = [
            self.python, "-m", "recadbench_eval.cli", "score-submission",
            "--zip", str(zip_path), "--out-dir", str(out_dir), "--submission-id", submission_id,
            "--layer", self.layer, "--timeout", str(self.timeout_s),
        ]  # fmt: skip
        if self.tasks_file:
            cmd += ["--tasks-file", str(self.tasks_file)]
        return cmd

    def dispatch(self, submission_id: str, sha: str, zip_ref: str) -> str:
        if not SUBMISSION_ID_RE.match(submission_id):
            raise ValueError("invalid submission id")
        if not self.gt_dir:
            raise RuntimeError("evaluator is not configured (RECADBENCH_GT_DIR is not set)")
        out = self.out_dir(submission_id)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").unlink(missing_ok=True)
        env = dict(os.environ, RECADBENCH_GT_DIR=self.gt_dir)
        log = open(out / "score.log", "w", encoding="utf-8")  # noqa: SIM115 - owned by the child
        try:
            proc = self._popen(
                self.command(zip_ref, out, submission_id),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            log.close()
        job_id = f"local-{submission_id}"
        self._procs[job_id] = proc
        return job_id

    def status(self, job_id: str) -> str:
        proc = self._procs.get(job_id)
        sid = job_id.removeprefix("local-")
        ok_id = SUBMISSION_ID_RE.match(sid)
        summary = self.out_dir(sid).joinpath("summary.json") if ok_id else None
        if proc is None:
            # Space restarted: the child handle is gone; a finished run left its summary behind.
            return COMPLETED if summary is not None and summary.is_file() else UNKNOWN
        rc = proc.poll()
        if rc is None:
            return RUNNING
        if rc == 0 and summary is not None and summary.is_file():
            return COMPLETED
        return ERROR

    def collect(self, submission_id: str, job_id: str, store: Store) -> dict | None:
        out = self.out_dir(submission_id)
        try:
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (out / "public").is_dir():
            store.put_report(submission_id, out / "public")
        return summary if isinstance(summary, dict) else None


# --- HF Jobs ----------------------------------------------------------------------------------

_JOB_SCRIPT = """\
import json, os, pathlib, subprocess, sys
from huggingface_hub import HfApi, hf_hub_download, snapshot_download
SUB_REPO = {sub_repo!r}
GT_REPO = {gt_repo!r}
SID = {sid!r}
SHA = {sha!r}
TASK_IDS = {task_ids!r}
TOKEN_VARS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_HUB_TOKEN")
tok = os.environ["HF_TOKEN"]
api = HfApi(token=tok)
zip_path = hf_hub_download(SUB_REPO, "submissions/" + SHA + ".zip", repo_type="dataset", token=tok)
gt = snapshot_download(GT_REPO, repo_type="dataset", token=tok, local_dir="/tmp/rcb-gt")
# The scorer parses untrusted STEP files: its processes never see the token.
for k in TOKEN_VARS:
    os.environ.pop(k, None)
env = {{k: v for k, v in os.environ.items() if k not in TOKEN_VARS}}
env["RECADBENCH_GT_DIR"] = os.path.join(gt, "parts")
out = "/tmp/rcb-out"
cmd = [sys.executable, "-m", "recadbench_eval.cli", "score-submission", "--zip", zip_path,
       "--out-dir", out, "--submission-id", SID, "--layer", {layer!r}, "--timeout", {timeout_s!r}]
if TASK_IDS:
    tasks_file = "/tmp/rcb-tasks.txt"
    pathlib.Path(tasks_file).write_text("".join(t + chr(10) for t in TASK_IDS), encoding="utf-8")
    cmd += ["--tasks-file", tasks_file]
rc = subprocess.run(cmd, env=env).returncode
summary = pathlib.Path(out, "summary.json")
if rc != 0 or not summary.is_file():
    print("score-submission failed, rc=%s" % rc)
    sys.exit(rc or 1)
public = pathlib.Path(out, "public")
if public.is_dir():
    api.upload_folder(repo_id=SUB_REPO, repo_type="dataset", folder_path=str(public),
                      path_in_repo="reports/" + SID, commit_message="report " + SID)
api.upload_file(path_or_fileobj=str(summary), path_in_repo="jobs/" + SID + "/summary.json",
                repo_id=SUB_REPO, repo_type="dataset", commit_message="summary " + SID)
print("done", SID)
"""


# score-submission runs two child processes per task in sequence (score, then heatmap), each
# with --timeout; the HF Job timeout must cover all of them plus downloads and uploads.
CHILDREN_PER_TASK = 2
DEFAULT_N_TASKS = 10  # evaluator default task list (the 10 public dev tasks)
JOB_RESERVE_S = 300  # zip + GT download, report upload, interpreter start-up
MIN_TASK_TIMEOUT_S = 30


def parse_duration_s(timeout: str | int | float) -> int:
    """HF Jobs timeout ('30m', '1h', '90s', 1800) in seconds."""
    if isinstance(timeout, int | float) and not isinstance(timeout, bool):
        return int(timeout)
    t = str(timeout).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if t and t[-1] in units:
        return int(float(t[:-1]) * units[t[-1]])
    return int(float(t))


def per_task_timeout_s(
    job_timeout: str | int | float, n_tasks: int, reserve_s: int = JOB_RESERVE_S
) -> int:
    """Per-child timeout with ``n_tasks * CHILDREN_PER_TASK * t + reserve_s <= job timeout``.

    Raises ValueError when that leaves under MIN_TASK_TIMEOUT_S (raise the job timeout).
    """
    n = max(1, int(n_tasks))
    t = (parse_duration_s(job_timeout) - reserve_s) // (n * CHILDREN_PER_TASK)
    if t < MIN_TASK_TIMEOUT_S:
        raise ValueError("HF Job timeout is too short for the per-task scoring budget")
    return int(t)


def build_job_script(
    sub_repo: str, gt_repo: str, submission_id: str, sha: str, layer: str = "open",
    timeout_s: int = 900, task_ids: list[str] | tuple[str, ...] | None = None,
) -> str:  # fmt: skip
    """Python source the HF Job runs. Only public/ and summary.json are uploaded.

    ``task_ids`` (the Space's task set) is passed to score-submission as --tasks-file; None
    keeps the evaluator default.
    """
    if not SUBMISSION_ID_RE.match(submission_id) or not SHA256_RE.match(sha):
        raise ValueError("invalid submission id or sha256")
    ids = [str(t) for t in (task_ids or ())]
    if any(not TASK_ID_RE.match(t) for t in ids):
        raise ValueError("invalid task id")
    return _JOB_SCRIPT.format(
        sub_repo=sub_repo,
        gt_repo=gt_repo,
        sid=submission_id,
        sha=sha,
        layer=layer,
        timeout_s=str(int(timeout_s)),
        task_ids=ids,
    )


class HFJobsDispatcher(Dispatcher):
    def __init__(
        self,
        submissions_repo: str,
        gt_repo: str,
        image: str,
        token: str | None,
        flavor: str = "cpu-upgrade",
        timeout: str = "30m",
        namespace: str | None = None,
        api: Any = None,
        task_ids: list[str] | None = None,
    ):
        """``token``: the Space secret. Use a fine-grained token: write on the submissions repo,
        read on the GT repo, nothing else. It is removed from the scorer's environment.

        ``task_ids``: the Space's task set (RCB_TASKS_FILE); None = evaluator default. The
        per-task child timeout is derived from ``timeout`` so a slow submission ends with
        per-task ``error`` rows instead of the whole job being killed.
        """
        if api is None:
            from huggingface_hub import HfApi

            api = HfApi(token=token)
        self.api = api
        self.task_ids = list(task_ids) if task_ids else None
        n_tasks = len(self.task_ids) if self.task_ids else DEFAULT_N_TASKS
        self.task_timeout_s = per_task_timeout_s(timeout, n_tasks)
        self.sub_repo = submissions_repo
        self.gt_repo = gt_repo
        self.image = image
        self.token = token
        self.flavor = flavor
        self.timeout = timeout
        self.namespace = namespace

    def dispatch(self, submission_id: str, sha: str, zip_ref: str) -> str:
        script = build_job_script(
            self.sub_repo, self.gt_repo, submission_id, sha,
            timeout_s=self.task_timeout_s, task_ids=self.task_ids,
        )  # fmt: skip
        job = self.api.run_job(
            image=self.image,
            command=["python", "-c", script],
            secrets={"HF_TOKEN": self.token or ""},
            flavor=self.flavor,
            timeout=self.timeout,
            namespace=self.namespace,
        )
        job_id = getattr(job, "id", None) or (job.get("id") if isinstance(job, dict) else None)
        if not job_id:
            raise RuntimeError("HF Jobs returned no job id")
        return str(job_id)

    def status(self, job_id: str) -> str:
        try:
            info = self.api.inspect_job(job_id=job_id, namespace=self.namespace)
        except Exception:  # noqa: BLE001 - network / unknown job
            return UNKNOWN
        st = getattr(info, "status", None)
        stage = str(getattr(st, "stage", st) or "").upper()
        if stage.startswith("JOBSTAGE."):
            stage = stage.split(".", 1)[1]
        return {
            "COMPLETED": COMPLETED,
            "RUNNING": RUNNING,
            "ERROR": ERROR,
            "CANCELED": ERROR,
            "DELETED": ERROR,
        }.get(stage, QUEUED if stage else UNKNOWN)

    def collect(self, submission_id: str, job_id: str, store: Store) -> dict | None:
        getter = getattr(store, "get_job_summary", None)
        return getter(submission_id) if getter else None


# --- polling ----------------------------------------------------------------------------------


def refresh_jobs(store: Store, dispatcher: Dispatcher) -> int:
    """Advance pending/scoring rows whose job finished. Returns the number of rows changed."""
    changed = 0
    for row in store.list_results():
        if row.get("status") not in ("pending", "scoring") or not row.get("job_id"):
            continue
        sid, job_id = row["submission_id"], row["job_id"]
        st = dispatcher.status(job_id)
        if st == COMPLETED:
            summary = dispatcher.collect(sid, job_id, store)
            if summary is None:
                store.update_result(
                    sid, status="failed", status_message="scoring finished without a summary"
                )
            else:
                store.update_result(
                    sid, status="done", status_message="", **row_fields_from_summary(summary)
                )
            changed += 1
        elif st == ERROR:
            store.update_result(sid, status="failed", status_message="scoring job failed")
            changed += 1
        elif st == RUNNING and row.get("status") == "pending":
            store.update_result(sid, status="scoring")
            changed += 1
    return changed
