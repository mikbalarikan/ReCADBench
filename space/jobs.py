"""Scoring dispatch for the ReCADBench Open Space.

A ``Dispatcher`` starts one scoring run per submission and reports its state:

* ``LocalDispatcher``: ``python -m recadbench_eval.cli score-submission`` as a local subprocess,
  GT from ``RECADBENCH_GT_DIR`` (local mode, and the deployed Space in ``space`` mode). The child
  never sees access tokens or OAuth variables (``scrubbed_env``). ``ScoringQueue`` runs at most
  ``max_parallel`` children; other uploads wait as ``pending`` rows without a job id and start
  first in, first out (the queue lives in the store, so it survives a restart).
* ``HFJobsDispatcher``: a Hugging Face Job (``huggingface_hub.run_job``) on the eval image
  (``space/Dockerfile.eval``). The job downloads the zip and the private GT dataset, runs
  ``score-submission`` and uploads ONLY ``public/`` (to ``reports/<id>/``) and ``summary.json``
  (to ``jobs/<id>/summary.json``) into the submissions repo. ``private/`` never leaves the job.
  Job timeout: the ``timeout`` argument, else ``RCB_JOB_TIMEOUT`` (e.g. ``3h``), else 30m.

The Space stays the single writer of ``results.jsonl``: ``refresh_jobs`` polls running rows and
merges finished summaries through the store (row allowlist applies).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from store import SHA256_RE, SUBMISSION_ID_RE, TASK_ID_RE, Store, waiting_rows

# job states returned by Dispatcher.status
QUEUED, RUNNING, COMPLETED, ERROR, UNKNOWN = "queued", "running", "completed", "error", "unknown"

# Never passed to a scorer child: it parses untrusted STEP files.
TOKEN_ENV_VARS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_HUB_TOKEN")
SECRET_ENV_PREFIXES = ("OAUTH_",)
NOT_CONFIGURED = "scoring not configured"
INTERRUPTED = "scoring was interrupted by a restart; upload the same zip again to retry"


def is_secret_env(name: str) -> bool:
    """Token / OAuth variables (the contract set) plus any other ``*TOKEN*`` / ``*SECRET*``."""
    up = name.upper()
    return (
        up in TOKEN_ENV_VARS
        or up.startswith(SECRET_ENV_PREFIXES)
        or "TOKEN" in up
        or "SECRET" in up
    )


def scrubbed_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """``base`` (default ``os.environ``) without access tokens and OAuth variables."""
    base = os.environ if base is None else base
    return {k: v for k, v in base.items() if not is_secret_env(k)}


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


def _abs(p: str | os.PathLike) -> str:
    """``p`` made absolute (the scorer runs in its own folder); rooted paths are kept as is."""
    s = os.fspath(p)
    if not s or os.path.isabs(s) or s.startswith(("/", "\\")):
        return s
    return os.path.abspath(s)


def kill_tree(proc: Any) -> None:
    """Kill a scorer and its own children: ``dispatch`` starts it as a session leader, so its
    process group id is its pid (POSIX). Elsewhere, or for a fake process, only ``proc``."""
    pid = getattr(proc, "pid", None)
    if os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, getattr(signal, "SIGKILL", 9))
        except OSError:  # not a group leader / already gone
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 - already gone
        pass
    try:
        proc.wait(timeout=5)  # reap it: no zombie
    except Exception:  # noqa: BLE001
        pass


def reap_group(pgid: int) -> bool:
    """PID 1 only (the app in the Space image has no init): reap the exited members of a
    finished scorer's process group, which the kernel re-parents to PID 1. Never
    ``waitpid(-1)``, which would take the exit status of a running scorer from ``Popen``.
    Returns True when the group has no child left (nothing more to reap)."""
    if os.name != "posix" or os.getpid() != 1:
        return True
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            return True
        except OSError:
            return True
        if pid == 0:  # members still running: try again on a later tick
            return False


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
        max_run_s: float | None = None,
        clock: Any = time.monotonic,
    ):
        """``max_run_s``: a child still running after this long is killed with its process
        group and reported as failed (default: the per-task budget of 10 tasks, two children
        each, plus a reserve), so a hung run cannot block the queue forever.

        The child runs isolated (``python -I``: no user site, no cwd on ``sys.path``, no
        ``PYTHON*`` variables) in its own output folder, as the leader of a new session."""
        self.work_root = Path(work_root).absolute()
        gt_dir = gt_dir if gt_dir is not None else os.environ.get("RECADBENCH_GT_DIR")
        self.gt_dir = _abs(gt_dir) if gt_dir else gt_dir
        self.python = python
        self.tasks_file = tasks_file
        self.layer = layer
        self.timeout_s = timeout_s
        self._popen = popen
        self._procs: dict[str, Any] = {}  # running (or not yet polled) children
        self._started: dict[str, float] = {}
        self._exit: dict[str, int] = {}  # exit code of children that are done (or killed)
        self._groups: set[int] = set()  # process groups of finished scorers, still to reap
        self._clock = clock
        if max_run_s is None:
            max_run_s = DEFAULT_N_TASKS * CHILDREN_PER_TASK * timeout_s + JOB_RESERVE_S
        self.max_run_s = max_run_s

    @property
    def configured(self) -> bool:
        """False without a GT folder: uploads then wait as pending ("scoring not configured")."""
        return bool(self.gt_dir)

    def active(self) -> int:
        """Scorer children started by this process that are still running."""
        # a snapshot: the poller thread may dispatch (insert) while a handler counts
        return sum(1 for p in list(self._procs.values()) if p.poll() is None)

    def out_dir(self, submission_id: str) -> Path:
        return self.work_root / submission_id

    def command(self, zip_path: str, out_dir: Path, submission_id: str) -> list[str]:
        cmd = [
            self.python, "-I", "-m", "recadbench_eval.cli", "score-submission",
            "--zip", _abs(zip_path), "--out-dir", _abs(out_dir), "--submission-id", submission_id,
            "--layer", self.layer, "--timeout", str(self.timeout_s),
        ]  # fmt: skip
        if self.tasks_file:
            cmd += ["--tasks-file", _abs(self.tasks_file)]
        return cmd

    def _done(self, job_id: str, rc: int) -> None:
        """Forget the handle of a finished (already waited for) child; keep its exit code and
        its process group for ``reap``."""
        self._exit[job_id] = rc
        proc = self._procs.pop(job_id, None)
        self._started.pop(job_id, None)
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int) and pid > 0:
            self._groups.add(pid)

    def _kill(self, job_id: str) -> None:
        proc = self._procs.get(job_id)
        if proc is not None:
            kill_tree(proc)
        self._done(job_id, -9)

    def reap(self, live_job_ids: set[str] | frozenset[str]) -> list[str]:
        """Kill and forget every child that ran past ``max_run_s`` or whose job id is no longer
        on a pending/scoring row (an admin deleted the row, the upload failed after dispatch),
        so a lost child never holds a scorer slot. Then reap the exited members of finished
        process groups (PID 1). Returns the killed job ids."""
        now = self._clock()
        killed = []
        for job_id, proc in list(self._procs.items()):
            if proc.poll() is not None:
                if job_id not in live_job_ids:
                    self._done(job_id, proc.poll())
                continue
            t0 = self._started.get(job_id)
            late = bool(self.max_run_s) and t0 is not None and now - t0 > self.max_run_s
            if late or job_id not in live_job_ids:
                self._kill(job_id)
                killed.append(job_id)
        running = {getattr(p, "pid", None) for p in self._procs.values()}
        for pgid in list(self._groups):
            # a running scorer's pid can equal an old group id only after that group is gone
            if pgid in running or reap_group(pgid):
                self._groups.discard(pgid)
        return killed

    def dispatch(self, submission_id: str, sha: str, zip_ref: str) -> str:
        if not SUBMISSION_ID_RE.match(submission_id):
            raise ValueError("invalid submission id")
        if not self.gt_dir:
            raise RuntimeError("evaluator is not configured (RECADBENCH_GT_DIR is not set)")
        job_id = f"local-{submission_id}"
        if job_id in self._procs:  # a lost run of a deleted row: never two in one folder
            self._kill(job_id)
        out = self.out_dir(submission_id)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").unlink(missing_ok=True)
        # The scorer parses untrusted STEP files: no access token, no OAuth client secret.
        env = scrubbed_env()
        env["RECADBENCH_GT_DIR"] = str(self.gt_dir)
        log = open(out / "score.log", "w", encoding="utf-8")  # noqa: SIM115 - owned by the child
        try:
            proc = self._popen(
                self.command(zip_ref, out, submission_id),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(out),
                start_new_session=True,  # POSIX: its own process group, killed as a whole
            )
        finally:
            log.close()
        self._exit.pop(job_id, None)
        self._procs[job_id] = proc
        self._started[job_id] = self._clock()
        return job_id

    def status(self, job_id: str) -> str:
        sid = job_id.removeprefix("local-")
        ok_id = SUBMISSION_ID_RE.match(sid)
        summary = self.out_dir(sid).joinpath("summary.json") if ok_id else None
        rc = self._exit.get(job_id)
        if rc is None:
            proc = self._procs.get(job_id)
            if proc is None:
                # Space restarted: the handle is gone; a finished run left its summary behind.
                return COMPLETED if summary is not None and summary.is_file() else UNKNOWN
            rc = proc.poll()
            if rc is None:
                t0 = self._started.get(job_id)
                if self.max_run_s and t0 is not None and self._clock() - t0 > self.max_run_s:
                    self._kill(job_id)
                    return ERROR
                return RUNNING
            self._done(job_id, rc)
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
        # the extracted STEPs (up to 50 MB per task) are not needed once the report is out
        shutil.rmtree(out / "runs", ignore_errors=True)
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
# HF Job timeout when neither the constructor nor RCB_JOB_TIMEOUT sets one. 30m leaves only 75 s
# per child for 10 tasks, too short for the slowest dev task: deploy sets RCB_JOB_TIMEOUT.
DEFAULT_JOB_TIMEOUT = "30m"
JOB_TIMEOUT_ENV = "RCB_JOB_TIMEOUT"


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
        timeout: str | None = None,
        namespace: str | None = None,
        api: Any = None,
        task_ids: list[str] | None = None,
    ):
        """``token``: the Space secret. Use a fine-grained token with exactly: write on the
        submissions repo, read on the GT repo and on the eval image Space, and permission to
        start Jobs in the namespace. It is removed from the scorer's environment.

        ``timeout``: HF Job timeout ('3h', '90m', seconds); None = ``RCB_JOB_TIMEOUT`` or
        DEFAULT_JOB_TIMEOUT. ``task_ids``: the Space's task set (RCB_TASKS_FILE); None =
        evaluator default. The per-task child timeout is derived from the job timeout so a slow
        submission ends with per-task ``error`` rows instead of the whole job being killed.
        """
        if timeout is None:
            timeout = os.environ.get(JOB_TIMEOUT_ENV, "").strip() or DEFAULT_JOB_TIMEOUT
        try:
            parse_duration_s(timeout)
        except ValueError as e:
            raise ValueError(f"invalid HF Job timeout {timeout!r} ({JOB_TIMEOUT_ENV})") from e
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


# --- in-app scoring queue ---------------------------------------------------------------------

STAGED_ZIP_GRACE_S = 600  # an unreferenced staged zip younger than this may belong to an upload
#                           whose row is being written right now


class ScoringQueue:
    """FIFO scoring queue for a ``LocalDispatcher`` (local mode and the deployed Space).

    The queue lives in the store: a ``pending`` row without ``job_id`` is waiting (see
    ``store.waiting_rows``), so a restart keeps every waiting upload. At most ``max_parallel``
    scorer children run at once; ``pump`` starts the oldest waiting rows when a slot is free.
    Each scorer reads a LOCAL copy of the zip in ``zip_dir`` (``stage``; after a restart it is
    fetched again from the store). One lock serialises ``pump`` / ``tick`` / ``recover``
    between the poller thread and Gradio handlers, so a row is never dispatched twice.

    ``tick`` runs ``recover`` first until one call succeeds (a Hub error at boot must not
    leave rows of the previous process ``scoring`` forever); ``after_recover`` (the startup
    sweep) runs inside that same successful recovery.
    """

    def __init__(
        self,
        store: Store,
        dispatcher: LocalDispatcher,
        max_parallel: int = 1,
        zip_dir: str | os.PathLike | None = None,
        after_recover: Callable[[], Any] | None = None,
    ):
        self.store = store
        self.dispatcher = dispatcher
        self.max_parallel = max(1, int(max_parallel))
        self.zip_dir = Path(zip_dir) if zip_dir else Path(dispatcher.work_root) / "zips"
        self.after_recover = after_recover
        self.recovered = False
        # True while a background poller ticks: page events then never do queue work
        self.polled = False
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return bool(getattr(self.dispatcher, "configured", True))

    # zips
    def _zip_path(self, sha: str) -> Path:
        if not isinstance(sha, str) or not SHA256_RE.match(sha):
            raise ValueError("invalid sha256")
        return self.zip_dir / f"{sha}.zip"

    def stage(self, sha: str, src: str | os.PathLike) -> Path:
        """Copy an upload into the work folder (the scorer never reads the Gradio temp file)."""
        dst = self._zip_path(sha)
        if dst.is_file():
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=dst.name, suffix=".tmp")
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return dst

    def local_zip(self, sha: str) -> Path:
        p = self._zip_path(sha)
        if p.is_file():
            return p
        src = self.store.local_zip(sha)
        if src is None:
            raise FileNotFoundError("submission zip not found in the store")
        return self.stage(sha, src)

    def _drop_finished_zips(self, rows: list[dict]) -> None:
        if not self.zip_dir.is_dir():
            return
        live = {r.get("sha256") for r in rows if r.get("status") in ("pending", "scoring")}
        now = time.time()
        for p in self.zip_dir.glob("*.zip"):
            try:
                if p.stem not in live and now - p.stat().st_mtime > STAGED_ZIP_GRACE_S:
                    p.unlink()
            except OSError:
                pass

    # queue
    def position(self, submission_id: str, rows: list[dict] | None = None) -> int | None:
        """1-based place among the waiting rows, None if not waiting."""
        rows = self.store.list_results() if rows is None else rows
        for i, r in enumerate(waiting_rows(rows), 1):
            if r.get("submission_id") == submission_id:
                return i
        return None

    def pump(self) -> list[str]:
        """Start waiting rows, oldest first, while fewer than ``max_parallel`` run."""
        with self._lock:
            return self._pump(self.store.list_results())

    def _pump(self, rows: list[dict]) -> list[str]:
        waiting = waiting_rows(rows)
        if not waiting:
            return []
        if not self.configured:  # accepted, kept pending; written once, not on every tick
            for r in waiting:
                if r.get("status_message") != NOT_CONFIGURED:
                    self.store.update_result(r["submission_id"], status_message=NOT_CONFIGURED)
            return []
        free = self.max_parallel - self.dispatcher.active()
        started: list[str] = []
        for r in waiting:
            if free <= 0:
                break
            sid, sha = r["submission_id"], str(r.get("sha256", ""))
            try:
                job_id = self.dispatcher.dispatch(sid, sha, str(self.local_zip(sha)))
            except Exception:  # noqa: BLE001 - a failed row, never a crash of the poller
                self.store.update_result(
                    sid, status="failed", status_message="could not start scoring"
                )
                continue
            self.store.update_result(sid, status="scoring", job_id=job_id, status_message="")
            started.append(sid)
            free -= 1
        return started

    def tick(self, blocking: bool = True) -> bool:
        """Merge finished runs, then start waiting ones. Returns False if skipped because
        another thread is ticking (``blocking=False``, used by page events)."""
        if not self._lock.acquire(blocking=blocking):
            return False
        try:
            if not self.recovered:
                self.recover()  # raises on a Hub error: the poller logs it and retries
            refresh_jobs(self.store, self.dispatcher)
            rows = self.store.list_results()
            # a child whose row is gone or no longer running (admin delete, failed upload) or
            # that ran past the watchdog is killed here, so it never blocks the queue
            reap = getattr(self.dispatcher, "reap", None)
            if reap is not None:
                reap({
                    str(r.get("job_id"))
                    for r in rows
                    if r.get("status") in ("pending", "scoring") and r.get("job_id")
                })  # fmt: skip
            self._pump(rows)
            self._drop_finished_zips(self.store.list_results())
        finally:
            self._lock.release()
        return True

    def recover(self) -> list[str]:
        """Startup after a restart: merge runs that finished, mark rows whose scorer is gone
        (no process, no summary) failed. Waiting rows stay queued and are started by ``pump``."""
        failed = []
        with self._lock:
            refresh_jobs(self.store, self.dispatcher)
            for r in self.store.list_results():
                if r.get("status") not in ("pending", "scoring") or not r.get("job_id"):
                    continue
                if self.dispatcher.status(r["job_id"]) == UNKNOWN:
                    self.store.update_result(
                        r["submission_id"], status="failed", status_message=INTERRUPTED
                    )
                    failed.append(r["submission_id"])
            if self.after_recover is not None:
                self.after_recover()
            self.recovered = True
        return failed

    def describe(self, submission_id: str) -> str:
        """User-facing status line right after an upload (no lock: the poller may be busy
        uploading a report; ``active`` counts over a snapshot of the running children)."""
        rows = self.store.list_results()
        row = next((r for r in rows if r.get("submission_id") == submission_id), None)
        sid = submission_id
        if row is None or row.get("status") == "failed":
            return f"Error: could not start scoring. Submission {sid} marked failed."
        if row.get("status") == "scoring":
            return f"Submission {sid} received and queued for scoring; scoring has started."
        if not self.configured:
            return (
                f"Submission {sid} received and queued, but scoring is not configured on this "
                "site yet; it stays pending and is scored once scoring is enabled."
            )
        pos = self.position(sid, rows)
        ahead = (pos or 1) - 1 + self.dispatcher.active()
        return (
            f"Submission {sid} received and queued for scoring; {ahead} run(s) ahead of it "
            "(one at a time, first in, first out)."
            if self.max_parallel == 1
            else f"Submission {sid} received and queued for scoring; {ahead} run(s) ahead of it."
        )
