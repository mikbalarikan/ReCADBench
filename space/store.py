"""Submission storage for the ReCADBench Open Space.

Two backends with the same interface:

* ``LocalStore(root)``: plain folders, for local smoke runs and tests.
* ``HubStore(submissions_repo, token)``: a (private) Hugging Face dataset repo.

Layout (both backends, relative to the root / repo):

    results.jsonl                 one leaderboard row per line (see ``clean_row``)
    submissions/<sha256>.zip      the uploaded zip, keyed by its sha256
    reports/<submission_id>/      the PUBLIC report folder (report.html, report.json, renders/)
    jobs/<submission_id>/summary.json   written by the HF scoring job (HubStore only)

Every row that enters ``results.jsonl`` goes through ``clean_row``: an allowlist of the contract
public row (FAZ2 contract section 3) plus the Space bookkeeping fields. Anything else (GT values,
paths, private result details) is dropped, never stored.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from filelock import FileLock

# --- row allowlist ----------------------------------------------------------------------------

PUBLIC_ROW_KEYS = (
    "submission_id",
    "sha256",
    "eval_version",
    "layer",
    "submitter_name",
    "submission_name",
    "agent_url",
    "model",
    "cad_backend",
    "harness",
    "human_in_loop",
    "open_total",
    "n_solved",
    "n_gate_ok",
    "per_task",
    "declared",
)
STORE_ROW_KEYS = (
    "status",  # pending | scoring | done | failed
    "submitted_at",  # ISO 8601 UTC, "...Z"
    "queued_at",  # ISO 8601 UTC, last (re)dispatch; used by the stale-job sweep
    "hf_user",  # Hugging Face username only
    "validated",  # bool, set by an admin
    "job_id",
    "reference",  # bool, reference rows (live-broadcast stations)
    "status_message",  # short public status text (failure reason)
)
ROW_KEYS = PUBLIC_ROW_KEYS + STORE_ROW_KEYS
PER_TASK_KEYS = (
    "status",
    "S1",
    "S2",
    "S3",
    "S4",
    "total",
    "gate_reason",
    "p_tight",
    "p_wide",
    "p95_mm",
    "key_dims",
    "face_ratio_bucket",
)
STATUSES = ("pending", "scoring", "done", "failed")
FACE_RATIO_BUCKETS = ("<0.25", "0.25-0.35", "0.35-1.5", "1.5-2", "2-3", ">3")
TASK_STATUSES = ("ok", "gate_fail", "missing", "error")

SUBMISSION_ID_RE = re.compile(r"^sub-[0-9a-f]{8,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_MAX_STR = 500


def _scalar(v: Any, max_len: int = _MAX_STR) -> Any:
    """Keep None/bool/int/float and short strings; drop everything else (returns a sentinel)."""
    if v is None or isinstance(v, bool | int | float):
        return v
    if isinstance(v, str):
        return v[:max_len]
    return _DROP


_DROP = object()


def _clean_per_task(per_task: Any) -> dict:
    out: dict[str, dict] = {}
    if not isinstance(per_task, dict):
        return out
    for tid, entry in per_task.items():
        if not isinstance(tid, str) or not TASK_ID_RE.match(tid) or not isinstance(entry, dict):
            continue
        clean: dict[str, Any] = {}
        for k in PER_TASK_KEYS:
            if k not in entry:
                continue
            v = entry[k]
            if k == "key_dims":
                dims = []
                for d in v if isinstance(v, list) else []:
                    if isinstance(d, dict) and isinstance(d.get("name"), str):
                        dims.append({"name": d["name"][:80], "ok": bool(d.get("ok"))})
                clean[k] = dims
            elif k == "status":
                if v in TASK_STATUSES:
                    clean[k] = v
            elif k == "face_ratio_bucket":
                if v in FACE_RATIO_BUCKETS:
                    clean[k] = v
            elif k == "gate_reason":
                if isinstance(v, str):
                    clean[k] = v[:200]
            else:
                if isinstance(v, int | float) and not isinstance(v, bool):
                    clean[k] = v
                elif v is None:
                    clean[k] = None
        out[tid] = clean
    return out


def _clean_declared(declared: Any, depth: int = 0) -> dict:
    """Declared info (features.json / run.json summaries): str keys, scalar / dict values."""
    out: dict[str, Any] = {}
    if not isinstance(declared, dict) or depth > 2:
        return out
    for k, v in declared.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, dict):
            out[k[:80]] = _clean_declared(v, depth + 1)
        else:
            s = _scalar(v, 200)
            if s is not _DROP:
                out[k[:80]] = s
    return out


def clean_row(row: dict) -> dict:
    """Return a copy of ``row`` restricted to the allowlist (contract section 3 + Space fields)."""
    out: dict[str, Any] = {}
    for k in ROW_KEYS:
        if k not in row:
            continue
        v = row[k]
        if k == "per_task":
            out[k] = _clean_per_task(v)
        elif k == "declared":
            out[k] = _clean_declared(v)
        elif k in ("human_in_loop", "validated", "reference"):
            out[k] = bool(v)
        elif k == "status":
            if v in STATUSES:
                out[k] = v
        else:
            s = _scalar(v)
            if s is not _DROP:
                out[k] = s
    return out


def _check_id(submission_id: str) -> str:
    if not isinstance(submission_id, str) or not SUBMISSION_ID_RE.match(submission_id):
        raise ValueError("invalid submission id")
    return submission_id


def _check_sha(sha: str) -> str:
    if not isinstance(sha, str) or not SHA256_RE.match(sha):
        raise ValueError("invalid sha256")
    return sha


def _parse_jsonl(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _dump_jsonl(rows: Iterable[dict]) -> str:
    return "".join(json.dumps(clean_row(r), sort_keys=True) + "\n" for r in rows)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- interface --------------------------------------------------------------------------------


class Store(ABC):
    """Rows + zips + public reports. Row writes are serialised by a file lock."""

    @abstractmethod
    def put_zip(self, sha: str, path: str | os.PathLike) -> str:
        """Store the zip under its sha256; return a backend reference (local path / repo path)."""

    @abstractmethod
    def zip_ref(self, sha: str) -> str:
        """Reference of a stored zip (same value ``put_zip`` returned)."""

    @abstractmethod
    def append_result(self, row: dict) -> dict:
        """Append a new row (cleaned); raises ValueError if the id already exists."""

    @abstractmethod
    def update_result(self, submission_id: str, **fields: Any) -> dict:
        """Merge ``fields`` into the row (cleaned); raises KeyError if it does not exist."""

    @abstractmethod
    def delete_result(self, submission_id: str) -> bool:
        """Remove the row (and its public report). Returns False if it did not exist."""

    @abstractmethod
    def list_results(self) -> list[dict]:
        """All rows, in insertion order."""

    @abstractmethod
    def put_report(self, submission_id: str, public_dir: str | os.PathLike) -> None:
        """Publish the PUBLIC report folder (``<out>/public``) of a submission."""

    @abstractmethod
    def get_report_html(self, submission_id: str) -> str | None:
        """The report.html text of a submission, or None."""

    def get_report_file(self, submission_id: str, relpath: str) -> Path | None:
        """A local path to another file of the public report (renders), or None."""
        return None

    def get_result(self, submission_id: str) -> dict | None:
        for r in self.list_results():
            if r.get("submission_id") == submission_id:
                return r
        return None


def _safe_relpath(relpath: str) -> str | None:
    """Accept only simple relative report paths like ``renders/x.png``."""
    if not isinstance(relpath, str) or "\\" in relpath or relpath.startswith("/"):
        return None
    parts = relpath.split("/")
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        return None
    return relpath


# --- local ------------------------------------------------------------------------------------


class LocalStore(Store):
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.results_path = self.root / "results.jsonl"
        self._lock = FileLock(str(self.results_path) + ".lock", timeout=60)

    # zips
    def put_zip(self, sha: str, path: str | os.PathLike) -> str:
        dst = self.root / "submissions" / f"{_check_sha(sha)}.zip"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(path, dst)
        return str(dst)

    def zip_ref(self, sha: str) -> str:
        return str(self.root / "submissions" / f"{_check_sha(sha)}.zip")

    # rows
    def _read(self) -> list[dict]:
        if not self.results_path.is_file():
            return []
        return _parse_jsonl(self.results_path.read_text(encoding="utf-8"))

    def list_results(self) -> list[dict]:
        with self._lock:
            return self._read()

    def append_result(self, row: dict) -> dict:
        row = clean_row(row)
        _check_id(row.get("submission_id", ""))
        with self._lock:
            rows = self._read()
            if any(r.get("submission_id") == row["submission_id"] for r in rows):
                raise ValueError("submission id already exists")
            rows.append(row)
            _atomic_write_text(self.results_path, _dump_jsonl(rows))
        return row

    def update_result(self, submission_id: str, **fields: Any) -> dict:
        _check_id(submission_id)
        with self._lock:
            rows = self._read()
            for i, r in enumerate(rows):
                if r.get("submission_id") == submission_id:
                    merged = clean_row({**r, **fields, "submission_id": submission_id})
                    rows[i] = merged
                    _atomic_write_text(self.results_path, _dump_jsonl(rows))
                    return merged
        raise KeyError(submission_id)

    def delete_result(self, submission_id: str) -> bool:
        _check_id(submission_id)
        with self._lock:
            rows = self._read()
            keep = [r for r in rows if r.get("submission_id") != submission_id]
            if len(keep) == len(rows):
                return False
            _atomic_write_text(self.results_path, _dump_jsonl(keep))
        shutil.rmtree(self.root / "reports" / submission_id, ignore_errors=True)
        return True

    # reports
    def put_report(self, submission_id: str, public_dir: str | os.PathLike) -> None:
        dst = self.root / "reports" / _check_id(submission_id)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(public_dir, dst)

    def get_report_html(self, submission_id: str) -> str | None:
        try:
            p = self.root / "reports" / _check_id(submission_id) / "report.html"
        except ValueError:
            return None
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def get_report_file(self, submission_id: str, relpath: str) -> Path | None:
        rel = _safe_relpath(relpath)
        if rel is None or not SUBMISSION_ID_RE.match(submission_id or ""):
            return None
        p = self.root / "reports" / submission_id / rel
        return p if p.is_file() else None


# --- hub --------------------------------------------------------------------------------------


class HubStore(Store):
    """Rows and files in a Hugging Face dataset repo (``repo_type="dataset"``).

    The Space is the only writer of ``results.jsonl``: scoring jobs upload their summary to
    ``jobs/<id>/summary.json`` and the Space merges it (``jobs.refresh_jobs``). Row writes take
    a local file lock and re-read the repo copy first, so concurrent Gradio handlers serialise.
    """

    RESULTS = "results.jsonl"

    def __init__(
        self,
        submissions_repo: str,
        token: str | None,
        cache_dir: str | os.PathLike | None = None,
        api: Any = None,
        cache_ttl_s: float = 10.0,
        clock: Any = time.monotonic,
    ):
        # list_results serves a copy of results.jsonl cached for ``cache_ttl_s`` (page views
        # would otherwise download it several times each); writes always re-read the repo
        # under the lock and refresh the cache with what they wrote.
        self.cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._rows_cache: tuple[float, list[dict]] | None = None
        if api is None:
            from huggingface_hub import HfApi

            api = HfApi(token=token)
        self.api = api
        self.repo = submissions_repo
        self.token = token
        self.cache = Path(cache_dir or tempfile.mkdtemp(prefix="rcb-hubstore-"))
        self.cache.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.cache / "results.jsonl.lock"), timeout=120)

    def _kw(self) -> dict:
        return {"repo_id": self.repo, "repo_type": "dataset", "token": self.token}

    def _download(self, filename: str) -> Path | None:
        if not self.api.file_exists(filename=filename, **self._kw()):
            return None
        p = self.api.hf_hub_download(
            filename=filename, local_dir=str(self.cache / "dl"), force_download=True, **self._kw()
        )
        return Path(p)

    def _upload_bytes(self, data: bytes, path_in_repo: str, message: str) -> None:
        self.api.upload_file(
            path_or_fileobj=data, path_in_repo=path_in_repo, commit_message=message, **self._kw()
        )

    # zips
    def put_zip(self, sha: str, path: str | os.PathLike) -> str:
        ref = self.zip_ref(sha)
        if not self.api.file_exists(filename=ref, **self._kw()):
            self.api.upload_file(
                path_or_fileobj=str(path),
                path_in_repo=ref,
                commit_message=f"submission {sha[:8]}",
                **self._kw(),
            )
        return ref

    def zip_ref(self, sha: str) -> str:
        return f"submissions/{_check_sha(sha)}.zip"

    # rows
    def _read(self) -> list[dict]:
        t0 = self._clock()
        p = self._download(self.RESULTS)
        rows = _parse_jsonl(p.read_text(encoding="utf-8")) if p else []
        cached = self._rows_cache
        if cached is None or cached[0] <= t0:  # never replace a newer write with this read
            self._rows_cache = (t0, copy.deepcopy(rows))
        return rows

    def _write(self, rows: list[dict], message: str) -> None:
        self._upload_bytes(_dump_jsonl(rows).encode("utf-8"), self.RESULTS, message)
        self._rows_cache = (self._clock(), [clean_row(r) for r in rows])

    def invalidate_cache(self) -> None:
        self._rows_cache = None

    def list_results(self) -> list[dict]:
        cached = self._rows_cache
        if cached is not None and self._clock() - cached[0] < self.cache_ttl_s:
            return copy.deepcopy(cached[1])
        return self._read()

    def append_result(self, row: dict) -> dict:
        row = clean_row(row)
        sid = _check_id(row.get("submission_id", ""))
        with self._lock:
            rows = self._read()
            if any(r.get("submission_id") == sid for r in rows):
                raise ValueError("submission id already exists")
            rows.append(row)
            self._write(rows, f"add {sid}")
        return row

    def update_result(self, submission_id: str, **fields: Any) -> dict:
        _check_id(submission_id)
        with self._lock:
            rows = self._read()
            for i, r in enumerate(rows):
                if r.get("submission_id") == submission_id:
                    rows[i] = clean_row({**r, **fields, "submission_id": submission_id})
                    self._write(rows, f"update {submission_id}")
                    return rows[i]
        raise KeyError(submission_id)

    def delete_result(self, submission_id: str) -> bool:
        _check_id(submission_id)
        with self._lock:
            rows = self._read()
            keep = [r for r in rows if r.get("submission_id") != submission_id]
            if len(keep) == len(rows):
                return False
            self._write(keep, f"delete {submission_id}")
        try:
            self.api.delete_folder(
                path_in_repo=f"reports/{submission_id}",
                commit_message=f"delete {submission_id}",
                **self._kw(),
            )
        except Exception:  # noqa: BLE001 - report folder may not exist
            pass
        return True

    # reports
    def put_report(self, submission_id: str, public_dir: str | os.PathLike) -> None:
        self.api.upload_folder(
            folder_path=str(public_dir),
            path_in_repo=f"reports/{_check_id(submission_id)}",
            commit_message=f"report {submission_id}",
            **self._kw(),
        )

    def get_report_html(self, submission_id: str) -> str | None:
        if not SUBMISSION_ID_RE.match(submission_id or ""):
            return None
        p = self._download(f"reports/{submission_id}/report.html")
        return p.read_text(encoding="utf-8") if p else None

    def get_report_file(self, submission_id: str, relpath: str) -> Path | None:
        rel = _safe_relpath(relpath)
        if rel is None or not SUBMISSION_ID_RE.match(submission_id or ""):
            return None
        return self._download(f"reports/{submission_id}/{rel}")

    def get_job_summary(self, submission_id: str) -> dict | None:
        p = self._download(f"jobs/{_check_id(submission_id)}/summary.json")
        if p is None:
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None
