"""Space-mode runtime of ReCADBench Open: scoring inside the public app Space (free cpu-basic).

``space_backends`` builds the store and the scorer for ``RCB_MODE=space``:

* store: ``HubStore(RCB_SUBMISSIONS_REPO, HF_TOKEN)`` (rows, zips and reports persist across
  restarts). Without ``HF_TOKEN`` / ``RCB_SUBMISSIONS_REPO`` the site still starts on an
  ephemeral ``LocalStore`` (uploads are accepted but not kept across restarts).
* GT: ``snapshot_download(RCB_GT_REPO, repo_type="dataset", token=HF_TOKEN)`` into
  ``$HOME/.rcb-gt`` at startup; ``RECADBENCH_GT_DIR`` for the scorer = ``$HOME/.rcb-gt/parts``.
  The folder must lie outside everything the site can serve (the app folder, the working
  directory, Gradio's upload and cache folders); if ``$HOME/.rcb-gt`` does not, a fresh private
  temp folder is used instead. Without ``RCB_GT_REPO`` a pre-provisioned ``RECADBENCH_GT_DIR``
  is accepted (host runs). If neither works the site still starts and uploads stay ``pending``
  with "scoring not configured".
* scorer: ``LocalDispatcher`` in ``$HOME/.rcb-work`` (same privacy rule): one subprocess per
  run without tokens or OAuth variables, at most ``RCB_MAX_PARALLEL`` (default 1) at once,
  ``RCB_TASK_TIMEOUT`` seconds per scorer child (default 900; deploy sets 420). A whole run is
  killed after ``RCB_MAX_RUN`` seconds (default 2700 = 45 min), so one upload cannot hold the
  single scorer slot for hours.
* GT retry: when the startup download fails, ``GtRetry`` (called by the poller) tries again
  every ``GT_RETRY_EVERY_S`` seconds and enables scoring once it succeeds.

``Poller`` is the background thread that advances the queue every ``POLL_EVERY_S`` seconds so
results land without page visits. ``harden_process`` (Linux) makes the app process
non-dumpable, so a scorer child (same uid) cannot read the app's environment or memory through
``/proc``; ``isolation_problem`` refuses the scorer when that failed or when another PID 1
(an init wrapper) holds the token in a readable ``/proc/1/environ``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jobs import CHILDREN_PER_TASK, DEFAULT_N_TASKS, JOB_RESERVE_S, LocalDispatcher
from store import HubStore, LocalStore, Store

GT_DIRNAME = ".rcb-gt"
WORK_DIRNAME = ".rcb-work"
POLL_EVERY_S = 20.0
DEFAULT_MAX_PARALLEL = 1
DEFAULT_TASK_TIMEOUT_S = 900
MIN_TASK_TIMEOUT_S = 60
# Space mode: a whole run (10 tasks x 2 children) is killed after this long. Real runs take
# about 30 s per task on a desktop CPU; 45 min leaves ample room on cpu-basic and bounds what
# one upload can take from the single scorer slot.
DEFAULT_MAX_RUN_S = 2700
MIN_MAX_RUN_S = 300
GT_RETRY_EVERY_S = 600.0
# entries of a foreign PID 1 environment that must never be readable by the scorer
SECRET_ENV_ENTRIES = (b"HF_TOKEN=", b"HUGGING_FACE_HUB_TOKEN=", b"HF_HUB_TOKEN=",
                      b"OAUTH_CLIENT_SECRET=")  # fmt: skip


def _log(msg: str) -> None:
    print(f"[rcb] {msg}", file=sys.stderr, flush=True)


log = _log


def max_parallel(env: Mapping[str, str]) -> int:
    try:
        return max(1, int(str(env.get("RCB_MAX_PARALLEL") or DEFAULT_MAX_PARALLEL).strip()))
    except ValueError:
        return DEFAULT_MAX_PARALLEL


def task_timeout_s(env: Mapping[str, str]) -> int:
    """Seconds per scorer child (``RCB_TASK_TIMEOUT``, default the evaluator's 900, at least
    ``MIN_TASK_TIMEOUT_S``); the run watchdog (``max_run_s``) is derived from it."""
    try:
        t = int(str(env.get("RCB_TASK_TIMEOUT") or DEFAULT_TASK_TIMEOUT_S).strip())
    except ValueError:
        return DEFAULT_TASK_TIMEOUT_S
    return max(MIN_TASK_TIMEOUT_S, t)


def max_run_s(env: Mapping[str, str], timeout_s: int) -> int:
    """Run watchdog of space mode: the per-child budget (10 tasks x 2 children + reserve),
    capped by ``RCB_MAX_RUN`` (default ``DEFAULT_MAX_RUN_S``, at least ``MIN_MAX_RUN_S``)."""
    derived = DEFAULT_N_TASKS * CHILDREN_PER_TASK * timeout_s + JOB_RESERVE_S
    try:
        cap = int(str(env.get("RCB_MAX_RUN") or DEFAULT_MAX_RUN_S).strip())
    except ValueError:
        cap = DEFAULT_MAX_RUN_S
    return min(derived, max(MIN_MAX_RUN_S, cap))


def home_dir(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def _resolve(p: str | os.PathLike, base: Path | None = None) -> Path:
    p = Path(p)
    if not p.is_absolute() and base is not None:
        p = base / p
    return Path(os.path.realpath(p))


def served_roots(env: Mapping[str, str], app_dir: Path, cwd: Path | None = None) -> list[Path]:
    """Folders the site may serve files from (or that Gradio treats as its own)."""
    cwd = _resolve(cwd or Path.cwd())
    roots = [
        cwd,
        _resolve(app_dir),
        _resolve(env.get("GRADIO_TEMP_DIR") or Path(tempfile.gettempdir()) / "gradio"),
        _resolve(env.get("GRADIO_EXAMPLES_CACHE") or ".gradio/cached_examples", cwd),
    ]
    # a filesystem root as working directory (Docker default "/") would contain everything
    return [r for r in roots if r != Path(r.anchor)]


def _inside(a: Path, b: Path) -> bool:
    """a == b or a below b (case-insensitive on Windows)."""
    a_s, b_s = os.path.normcase(str(a)), os.path.normcase(str(b))
    try:
        return os.path.commonpath([a_s, b_s]) == b_s
    except ValueError:  # different drives
        return False


def is_private(path: str | os.PathLike, roots: list[Path]) -> bool:
    p = _resolve(path)
    return not any(_inside(p, r) or _inside(r, p) for r in roots)


def private_dir(preferred: Path, roots: list[Path], prefix: str) -> Path:
    """``preferred`` if it lies outside every served root, else a fresh private temp folder."""
    cand = _resolve(preferred)
    if not is_private(cand, roots):
        _log(f"{preferred.name} would be inside a served folder; using a private temp folder")
        cand = _resolve(tempfile.mkdtemp(prefix=prefix))
        if not is_private(cand, roots):
            raise RuntimeError("no private folder available for the scorer")
    cand.mkdir(parents=True, exist_ok=True)
    try:
        cand.chmod(0o700)
    except OSError:
        pass
    return cand


def fetch_gt(
    repo: str | None, token: str | None, dest: Path, download: Callable | None = None
) -> Path | None:
    """Download the private GT dataset into ``dest``; return ``dest/parts`` or None."""
    if not repo or not token:
        return None
    if download is None:
        from huggingface_hub import snapshot_download as download
    try:
        download(repo_id=repo, repo_type="dataset", token=token, local_dir=str(dest))
    except Exception as e:  # noqa: BLE001 - the site must start anyway; never echo details
        _log(f"GT download failed ({type(e).__name__}); scoring is not configured")
        return None
    parts = dest / "parts"
    if not parts.is_dir():
        _log("GT download has no parts/ folder; scoring is not configured")
        return None
    return parts


def harden_process() -> bool:
    """Linux: PR_SET_DUMPABLE 0, so same-uid children cannot read /proc/<pid>/environ or mem
    of the app process (which holds HF_TOKEN). Best effort; False elsewhere."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE = 4
    except Exception:  # noqa: BLE001
        return False


def isolation_problem(
    hardened: bool,
    *,
    platform: str | None = None,
    pid: int | None = None,
    pid1_environ: str | os.PathLike = "/proc/1/environ",
) -> str | None:
    """Why the scorer must not run in this process (Linux, space mode), or None.

    The scorer runs as the app's uid, so the app must be non-dumpable (``hardened``) and must
    be PID 1 itself: any other PID 1 (tini, dumb-init, a shell) was started with HF_TOKEN and
    OAUTH_CLIENT_SECRET, and its ``/proc/1/environ`` stays readable for that uid. Only names
    are matched; no value ever reaches a message."""
    if not (platform or sys.platform).startswith("linux"):
        return None
    if not hardened:
        return "the app process could not be made non-dumpable"
    if (os.getpid() if pid is None else pid) == 1:
        return None
    try:
        data = Path(pid1_environ).read_bytes()
    except OSError:  # not readable for this uid: nothing to leak
        return None
    if any(e.startswith(SECRET_ENV_ENTRIES) for e in data.split(b"\0")):
        return "PID 1 is not the app and its environment (with the token) is readable"
    return None


class GtRetry:
    """Called by the poller: while the scorer has no GT folder, download the GT again every
    ``every_s`` seconds; on success the scorer is configured and waiting rows start."""

    def __init__(
        self,
        repo: str,
        token: str,
        dest: Path,
        download: Callable | None = None,
        every_s: float = GT_RETRY_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.repo, self.token, self.dest, self.download = repo, token, dest, download
        self.every_s = every_s
        self._clock = clock
        self._last = clock()  # the startup attempt has just run

    def __call__(self, dispatcher: LocalDispatcher) -> bool:
        if dispatcher.gt_dir:
            return False
        now = self._clock()
        if now - self._last < self.every_s:
            return False
        self._last = now
        parts = fetch_gt(self.repo, self.token, self.dest, download=self.download)
        if parts is None:
            return False
        dispatcher.gt_dir = str(parts)
        _log("GT download succeeded on retry; scoring is configured")
        return True


@dataclass
class SpaceSetup:
    store: Store
    dispatcher: LocalDispatcher
    gt_parts: Path | None
    private_dirs: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    gt_retry: GtRetry | None = None


def space_backends(
    env: Mapping[str, str],
    *,
    app_dir: Path,
    cwd: Path | None = None,
    hub_api: Any = None,
    gt_download: Callable | None = None,
) -> SpaceSetup:
    """Store + local scorer for ``RCB_MODE=space`` (see the module docstring)."""
    roots = served_roots(env, app_dir, cwd)
    home = home_dir(env)
    work = private_dir(home / WORK_DIRNAME, roots, "rcb-work-")
    token = (env.get("HF_TOKEN") or "").strip() or None
    repo = (env.get("RCB_SUBMISSIONS_REPO") or "").strip()
    problems: list[str] = []
    if token and repo:
        store: Store = HubStore(repo, token, api=hub_api, cache_dir=work / "hubstore")
    else:
        problems.append("HF_TOKEN or RCB_SUBMISSIONS_REPO is not set: uploads are kept in a "
                        "local folder that does not survive a restart")  # fmt: skip
        store = LocalStore(work / "store")
    private = [work]
    gt_parts = None
    gt_retry = None
    gt_repo = (env.get("RCB_GT_REPO") or "").strip()
    if gt_repo and token:
        gt_root = private_dir(home / GT_DIRNAME, roots, "rcb-gt-")
        private.append(gt_root)
        gt_parts = fetch_gt(gt_repo, token, gt_root, download=gt_download)
        gt_retry = GtRetry(gt_repo, token, gt_root, download=gt_download)
    elif not gt_repo and env.get("RECADBENCH_GT_DIR"):
        cand = _resolve(env["RECADBENCH_GT_DIR"])
        if cand.is_dir() and is_private(cand, roots):
            gt_parts = cand
            private.append(cand)
        else:
            problems.append("RECADBENCH_GT_DIR is missing or inside a served folder")
    if gt_parts is None:
        problems.append("scoring not configured (no GT: set HF_TOKEN and RCB_GT_REPO)")
    timeout_s = task_timeout_s(env)
    dispatcher = LocalDispatcher(
        work / "runs",
        gt_dir=str(gt_parts) if gt_parts is not None else "",
        tasks_file=env.get("RCB_TASKS_FILE") or None,
        timeout_s=timeout_s,
        max_run_s=max_run_s(env, timeout_s),
    )
    for p in problems:
        _log(p)
    return SpaceSetup(store, dispatcher, gt_parts, private, problems, gt_retry)


class Poller:
    """Daemon thread calling ``tick()`` now and then every ``every_s`` seconds."""

    def __init__(self, tick: Callable[[], Any], every_s: float = POLL_EVERY_S):
        self.tick = tick
        self.every_s = every_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep polling
                _log(f"poller: {type(e).__name__}")
            self._stop.wait(self.every_s)

    def start(self) -> Poller:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="rcb-poller", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
