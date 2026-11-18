"""Upload handling for the ReCADBench Open Space (pure logic; Gradio wiring is in app.py).

``handle_submission`` is the whole upload path: OAuth required, size check, structure check with
``recadbench_eval.submission.inspect_zip`` (stdlib only, nothing is extracted or executed),
sha256 dedupe, 3 uploads per Hugging Face user per UTC day, pending row, zip storage, dispatch.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jobs import Dispatcher, refresh_jobs
from store import Store

MAX_PER_DAY = 3
ZIP_LIMIT_BYTES = 300 * 2**20  # contract LIMITS["zip_bytes"]; the evaluator copy wins if importable

# The 10 public dev task ids (ReCADBench-data/splits/dev.txt). Override with RCB_TASKS_FILE.
DEFAULT_TASK_IDS = (
    "r385_hs01_motor_mount",
    "r385_dr02_wobble_plate",
    "r385_hs04_pump_head",
    "r385_hs02_diaphragm_frame",
    "r385_dr01_eccentric_hub",
    "od_g04_brewing_gasket_support",
    "od_h11_thermoblock",
    "od_h22_three_way_valve",
    "od_h24_flowmeter",
    "od_s03_steam_knob",
)

META_ROW_KEYS = (
    "submitter_name",
    "submission_name",
    "agent_url",
    "model",
    "cad_backend",
    "harness",
    "human_in_loop",
)


def load_task_ids(path: str | os.PathLike | None = None) -> list[str]:
    """Task ids from a one-id-per-line file (``#`` comments allowed), else the 10 dev ids."""
    path = path or os.environ.get("RCB_TASKS_FILE")
    if path and Path(path).is_file():
        ids = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
        if ids:
            return ids
    return list(DEFAULT_TASK_IDS)


def username(profile: Any) -> str | None:
    """HF username from a ``gr.OAuthProfile`` (or a dict in tests); None if not logged in."""
    if profile is None:
        return None
    if isinstance(profile, dict):
        name = profile.get("preferred_username") or profile.get("username")
    else:
        name = getattr(profile, "username", None) or getattr(profile, "preferred_username", None)
    return str(name) if name else None


def submission_id_for(sha: str, rows: list[dict] | None = None) -> str:
    """``sub-<sha[:8]>``; on a prefix collision with another zip's row, a longer prefix."""
    taken = {r.get("submission_id") for r in rows or () if r.get("sha256") != sha}
    for n in (8, 12, 16, 24, 32, 64):
        sid = f"sub-{sha[:n]}"
        if sid not in taken:
            return sid
    raise ValueError("submission id collision")


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def uploads_today(rows: list[dict], user: str, now: datetime) -> int:
    day = now.astimezone(UTC).date()
    n = 0
    for r in rows:
        if r.get("hf_user") != user or r.get("reference"):
            continue
        ts = _parse_iso(r.get("submitted_at"))
        if ts is not None and ts.astimezone(UTC).date() == day:
            n += 1
    return n


def _default_inspect() -> tuple[Callable, int]:
    from recadbench_eval import submission as sub  # stdlib-only module

    return sub.inspect_zip, int(sub.LIMITS["zip_bytes"])


def handle_submission(
    zip_path: str | os.PathLike | None,
    profile: Any,
    store: Store,
    dispatcher: Dispatcher,
    now: datetime | None = None,
    *,
    task_ids: list[str] | None = None,
    inspect: Callable | None = None,
    zip_limit: int | None = None,
) -> str:
    """Validate, record and dispatch one upload. Returns the status text shown to the user."""
    now = now or datetime.now(UTC)
    user = username(profile)
    if not user:
        return "Error: please sign in with Hugging Face before submitting."
    if not zip_path or not Path(zip_path).is_file():
        return "Error: no file uploaded."
    if inspect is None:
        inspect, limit = _default_inspect()
        zip_limit = zip_limit or limit
    zip_limit = zip_limit or ZIP_LIMIT_BYTES
    size = Path(zip_path).stat().st_size
    if size > zip_limit:
        return f"Error: the zip is larger than the {zip_limit // 2**20} MB limit."

    task_ids = task_ids or load_task_ids()
    try:
        sub = inspect(str(zip_path), task_ids)
    except ValueError as e:  # SubmissionError is a ValueError with a user-facing message
        return f"Error: {e}"
    if not sub.tasks:
        return "Error: no task folder contains an output.step."

    rows = store.list_results()
    existing = next((r for r in rows if r.get("sha256") == sub.sha256), None)
    if existing is not None and existing.get("status") != "failed":
        return (
            f"This zip was already submitted as {existing.get('submission_id')} "
            f"(status: {existing.get('status')})."
        )
    if existing is not None and existing.get("hf_user") != user and not is_admin(profile):
        return (
            f"Error: this zip was already submitted by another user as "
            f"{existing.get('submission_id')}; ask an admin to rescore it."
        )
    if uploads_today(rows, user, now) >= MAX_PER_DAY:
        return f"Error: limit of {MAX_PER_DAY} submissions per user per UTC day reached."

    if existing is not None:
        sid = existing["submission_id"]
    else:
        try:
            sid = submission_id_for(sub.sha256, rows)
        except ValueError:
            return "Error: could not assign a submission id; please try again later."
    meta = sub.meta if isinstance(sub.meta, dict) else {}
    row = {k: meta[k] for k in META_ROW_KEYS if k in meta}
    row.update(
        submission_id=sid,
        sha256=sub.sha256,
        layer="open",
        status="pending",
        submitted_at=_iso(now),
        queued_at=_iso(now),
        hf_user=user,
        validated=False,
        job_id="",
        status_message="",
    )
    try:
        if existing is not None:  # earlier attempt by the same user failed: retry in place
            if existing.get("hf_user"):
                row["hf_user"] = existing["hf_user"]  # an admin retry keeps the attribution
            store.update_result(sid, **{k: v for k, v in row.items() if k != "submission_id"})
        else:
            store.append_result(row)
    except (KeyError, ValueError):  # concurrent upload of a colliding / deleted id
        return "Error: could not record the submission; please try again."

    try:
        ref = store.put_zip(sub.sha256, zip_path)
        job_id = dispatcher.dispatch(sid, sub.sha256, ref)
    except Exception as e:  # noqa: BLE001 - shown as a failed row, never a traceback
        store.update_result(sid, status="failed", status_message="could not start scoring")
        why = type(e).__name__
        return f"Error: could not start scoring ({why}). Submission {sid} marked failed."
    store.update_result(sid, job_id=job_id)

    lines = [f"Submission {sid} received and queued for scoring."]
    n_missing = len(getattr(sub, "missing", []) or [])
    if n_missing:
        lines.append(f"{n_missing} task(s) without output.step will score 0.")
    for w in (getattr(sub, "warnings", []) or [])[:10]:
        lines.append(f"Warning: {w}")
    return "\n".join(lines)


def sweep_pending(
    store: Store,
    dispatcher: Dispatcher | None = None,
    max_age_h: float = 2,
    now: datetime | None = None,
) -> list[str]:
    """Startup sweep: merge finished jobs, then mark stale pending/scoring rows failed."""
    now = now or datetime.now(UTC)
    if dispatcher is not None:
        try:
            refresh_jobs(store, dispatcher)
        except Exception:  # noqa: BLE001 - a broken poll must not block the sweep
            pass
    swept = []
    cutoff = now - timedelta(hours=max_age_h)
    for r in store.list_results():
        if r.get("status") not in ("pending", "scoring"):
            continue
        ts = _parse_iso(r.get("queued_at")) or _parse_iso(r.get("submitted_at"))
        if ts is None or ts < cutoff:
            store.update_result(
                r["submission_id"], status="failed", status_message="scoring timed out"
            )
            swept.append(r["submission_id"])
    return swept


# --- admin ------------------------------------------------------------------------------------


def admins() -> set[str]:
    return {a.strip() for a in os.environ.get("RCB_ADMINS", "").split(",") if a.strip()}


def is_admin(profile: Any) -> bool:
    user = username(profile)
    return bool(user) and user in admins()


def admin_action(
    action: str,
    submission_id: str,
    profile: Any,
    store: Store,
    dispatcher: Dispatcher,
    now: datetime | None = None,
) -> str:
    """promote | demote | rescore | delete. The caller's username must be in RCB_ADMINS."""
    if not is_admin(profile):
        return "Error: not an admin."
    row = store.get_result(submission_id or "")
    if row is None:
        return "Error: unknown submission id."
    sid = row["submission_id"]
    if action == "promote":
        store.update_result(sid, validated=True)
        return f"{sid} marked validated."
    if action == "demote":
        store.update_result(sid, validated=False)
        return f"{sid} marked unvalidated."
    if action == "delete":
        store.delete_result(sid)
        return f"{sid} deleted."
    if action == "rescore":
        sha = row.get("sha256", "")
        try:
            job_id = dispatcher.dispatch(sid, sha, store.zip_ref(sha))
        except Exception as e:  # noqa: BLE001
            store.update_result(sid, status="failed", status_message="could not start scoring")
            return f"Error: rescore failed to start ({type(e).__name__})."
        store.update_result(
            sid,
            status="pending",
            job_id=job_id,
            status_message="",
            queued_at=_iso(now or datetime.now(UTC)),
        )
        return f"{sid} queued for rescoring."
    return "Error: unknown action."
