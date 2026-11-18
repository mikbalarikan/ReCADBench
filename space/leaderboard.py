"""Leaderboard views for the ReCADBench Open Space (pure functions, no Gradio import).

Input rows are store rows (``store.clean_row`` allowlist). Nothing here reads GT or private
results; the only numbers shown are the public row's scores.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from store import clean_row

LEADERBOARD_HEADERS = [
    "Rank",
    "Submission",
    "Submitter",
    "Model",
    "CAD backend",
    "Harness",
    "Open /95",
    "Solved",
    "Gate OK",
    "Status",
    "Human in loop",
    "Date",
    "Report",
]
STATUS_LABELS = {
    "pending": "queued",
    "scoring": "scoring",
    "done": "done",
    "failed": "failed",
}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _fmt(v: Any, nd: int = 1) -> str:
    x = _num(v)
    return "-" if x is None else f"{x:.{nd}f}"


def load_reference_rows(path: str | os.PathLike | None = None) -> list[dict]:
    """Reference rows (live-broadcast stations) from a JSON list; each is flagged reference."""
    path = path or os.environ.get("RCB_REFERENCE_FILE")
    if not path or not Path(path).is_file():
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for r in data if isinstance(data, list) else []:
        if isinstance(r, dict) and r.get("submission_id"):
            out.append(clean_row({**r, "reference": True, "status": r.get("status", "done")}))
    return out


def board_rows(rows: list[dict], include_unfinished: bool = True) -> list[dict]:
    """Rows to show, best Open total first; scored rows before queued/failed ones."""
    shown = [r for r in rows if include_unfinished or r.get("status") == "done"]

    def key(r: dict) -> tuple:
        done = r.get("status") == "done"
        total = _num(r.get("open_total"))
        score = total if total is not None else -1.0
        return (0 if done else 1, -score, r.get("submitted_at", ""))

    return sorted(shown, key=key)


def badges(row: dict) -> str:
    out = []
    if row.get("reference"):
        out.append("reference")
    out.append("validated" if row.get("validated") else "unvalidated")
    return ", ".join(out)


def table(rows: list[dict], report_base: str = "reports") -> tuple[list[str], list[list[str]]]:
    """(headers, rows) for the Open /95 table.

    Every column except the last is a Gradio ``str`` cell (rendered as text, never as markdown
    or HTML), so user strings go in as plain text. The last column (Report) is ``markdown`` and
    holds only links the Space builds from a validated submission id.
    """
    data = []
    rank = 0
    for r in board_rows(rows):
        done = r.get("status") == "done"
        if done:
            rank += 1
        sid = str(r.get("submission_id", ""))
        report = f"[report]({report_base}/{sid}.html)" if done and not r.get("reference") else ""
        name = str(r.get("submission_name") or sid)
        status = STATUS_LABELS.get(str(r.get("status")), "")
        if done:
            status = badges(r)
        data.append(
            [
                str(rank) if done else "",
                _plain_text(name),
                _plain_text(str(r.get("submitter_name") or "")),
                _plain_text(str(r.get("model") or "")),
                _plain_text(str(r.get("cad_backend") or "")),
                _plain_text(str(r.get("harness") or "")),
                _fmt(r.get("open_total")) if done else "-",
                str(r.get("n_solved", "-")) if done else "-",
                str(r.get("n_gate_ok", "-")) if done else "-",
                status,
                "yes" if r.get("human_in_loop") else "no",
                str(r.get("submitted_at") or "")[:10],
                report,
            ]
        )
    return list(LEADERBOARD_HEADERS), data


def _plain_text(s: str) -> str:
    """User string for a plain-text (``str``) table cell: one line, at most 80 chars."""
    return " ".join(s.splitlines())[:80]


def _md_text(s: str) -> str:
    """Neutralise markdown/HTML in user-supplied strings placed inside markdown (gr.Markdown)."""
    s = html.escape(s.replace("\n", " ").replace("|", "/"))
    for ch in "[]()*_`#!":
        s = s.replace(ch, "\\" + ch)
    return s[:80]


def _cell_color(total: float | None) -> str:
    if total is None:
        return "transparent"
    t = max(0.0, min(1.0, total / 95.0))
    # light red -> amber -> green, kept light so dark text stays readable
    hue = int(120 * t)
    return f"hsl({hue}, 65%, 78%)"


def heatmap_html(rows: list[dict], task_ids: list[str]) -> str:
    """Task x submission table of per-task Open totals (scored rows only), coloured cells."""
    done = [r for r in board_rows(rows, include_unfinished=False)]
    if not done:
        return "<p>No scored submissions yet.</p>"
    head = "".join(f"<th title='{html.escape(t)}'>{html.escape(_short(t))}</th>" for t in task_ids)
    body = []
    for r in done:
        per = r.get("per_task") or {}
        cells = []
        for t in task_ids:
            e = per.get(t) or {}
            st = e.get("status", "missing")
            total = _num(e.get("total"))
            label = _fmt(total) if st == "ok" or total is not None else st.replace("_", " ")
            cells.append(
                f"<td style='background:{_cell_color(total)};color:#111;text-align:center' "
                f"title='{html.escape(st)}'>{html.escape(label)}</td>"
            )
        name = html.escape(str(r.get("submission_name") or r.get("submission_id") or ""))
        flag = " (ref)" if r.get("reference") else ""
        body.append(
            f"<tr><td>{name}{flag}</td><td style='text-align:center'>"
            f"{_fmt(r.get('open_total'))}</td>{''.join(cells)}</tr>"
        )
    return (
        "<div style='overflow-x:auto'><table style='border-collapse:collapse;font-size:13px'>"
        f"<thead><tr><th>Submission</th><th>Open /95</th>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def _short(task_id: str) -> str:
    """'r385_hs01_motor_mount' -> 'hs01', 'od_h11_thermoblock' -> 'h11'."""
    parts = task_id.split("_")
    return parts[1] if len(parts) > 1 else task_id


# --- tasks ------------------------------------------------------------------------------------

# Only these task fields are ever shown (no dimensions, no GT).
TASK_FIELDS = ("id", "tolerance_class", "tier", "triangles", "known_defects")


def load_tasks(path: str | os.PathLike | None, task_ids: list[str]) -> list[dict]:
    """Task cards from the inputs dataset's tasks.json, restricted to TASK_FIELDS."""
    by_id: dict[str, dict] = {}
    if path and Path(path).is_file():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = []
        for t in data if isinstance(data, list) else []:
            if isinstance(t, dict) and isinstance(t.get("id"), str):
                by_id[t["id"]] = {k: t[k] for k in TASK_FIELDS if k in t}
    ids = task_ids or list(by_id)
    return [by_id.get(t, {"id": t}) for t in ids]


def task_card_html(task: dict, inputs_repo: str | None, preview_url: str | None = None) -> str:
    tid = str(task.get("id", ""))
    esc = html.escape
    rows = []
    if "tolerance_class" in task:
        rows.append(f"<b>Tolerance class</b>: {esc(str(task['tolerance_class']))}")
    if "tier" in task:
        rows.append(f"<b>Tier</b>: {esc(str(task['tier']))}")
    if "triangles" in task:
        rows.append(f"<b>Scan triangles</b>: {esc(str(task['triangles']))}")
    defects = task.get("known_defects") or []
    if isinstance(defects, str):
        defects = [defects]
    defects_html = "".join(f"<li>{esc(str(d))}</li>" for d in defects if d)
    links = ""
    if inputs_repo:
        base = f"https://huggingface.co/datasets/{inputs_repo}/resolve/main/parts/{tid}"
        links = (
            f"<a href='{esc(base)}/input.stl'>input.stl</a> | "
            f"<a href='{esc(base)}/task.yaml'>task.yaml</a>"
        )
    img = (
        f"<img src='{esc(preview_url)}' alt='scan preview' style='max-width:100%;height:auto'>"
        if preview_url
        else ""
    )
    return (
        "<div style='border:1px solid #8884;border-radius:8px;padding:10px;margin:6px'>"
        f"<h4 style='margin:0 0 6px 0'>{esc(tid)}</h4>{img}"
        f"<div>{'<br>'.join(rows)}</div>"
        + (f"<div><b>Known scan defects</b><ul>{defects_html}</ul></div>" if defects_html else "")
        + f"<div>{links}</div></div>"
    )
