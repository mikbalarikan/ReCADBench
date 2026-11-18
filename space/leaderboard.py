"""Leaderboard views for the ReCADBench Open Space (pure functions, no Gradio import).

Input rows are store rows (``store.clean_row`` allowlist). Nothing here reads GT or private
results; the only numbers shown are the public row's scores. Every user string that goes into
HTML passes through ``html.escape``; links are built only from validated ids or http(s) URLs.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from store import SUBMISSION_ID_RE, TASK_ID_RE, clean_row, waiting_rows

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
OPEN_MAX = 95.0
COMPONENTS = (("S1", 40), ("S2", 25), ("S3", 20), ("S4", 10))

esc = html.escape


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


def _text(v: Any, n: int = 80) -> str:
    """A user string for HTML: one line, at most ``n`` chars, escaped."""
    return esc(_plain_text(str(v or ""))[:n])


def safe_url(u: Any) -> str | None:
    """``u`` if it is a plain http(s) URL with a host, else None (never javascript:, data:, ...)."""
    if not isinstance(u, str) or not u or len(u) > 500 or any(c.isspace() for c in u):
        return None
    try:
        p = urlparse(u)
    except ValueError:
        return None
    return u if p.scheme in ("http", "https") and p.netloc else None


def _report_href(row: dict, report_base: str) -> str | None:
    sid = str(row.get("submission_id", ""))
    if row.get("status") != "done" or row.get("reference") or not SUBMISSION_ID_RE.match(sid):
        return None
    return f"{report_base}/{sid}.html"


# --- stats + leaderboard HTML ---------------------------------------------------------------


def stats(rows: list[dict], task_ids: list[str]) -> dict:
    """Header numbers: task count, scored submissions, validated count, best Open total."""
    done = [r for r in rows if r.get("status") == "done"]
    scored = [r for r in done if not r.get("reference")]
    best: tuple[float, dict] | None = None
    for r in done:
        t = _num(r.get("open_total"))
        if t is not None and (best is None or t > best[0]):
            best = (t, r)
    return {
        "n_tasks": len(task_ids),
        "n_scored": len(scored),
        "n_validated": sum(1 for r in scored if r.get("validated")),
        "best_open": best[0] if best else None,
        "best_name": str(best[1].get("submission_name") or best[1].get("submission_id") or "")
        if best
        else "",
    }


def stats_html(s: dict) -> str:
    best = s.get("best_open")
    best_v = f"{best:.1f}<small>/95</small>" if isinstance(best, float) else "&ndash;"
    best_s = _text(s.get("best_name"), 60) if best is not None else "no scored run yet"
    n_val = int(s.get("n_validated") or 0)
    tiles = [
        ("var(--rcb-orange)", str(int(s.get("n_tasks") or 0)), "Tasks",
         "real 3D scans, public dev set"),
        ("var(--rcb-cyan)", str(int(s.get("n_scored") or 0)), "Submissions scored",
         f"{n_val} validated"),
        ("var(--rcb-lime)", best_v, "Best Open", best_s),
    ]  # fmt: skip
    cells = "".join(
        f"<div class='rcb-stat' style='--accent:{a}'><div class='v'>{v}</div>"
        f"<div class='l'>{lab}</div><div class='s'>{sub}</div></div>"
        for a, v, lab, sub in tiles
    )
    return f"<div class='rcb rcb-stats'>{cells}</div>"


def empty_state_html() -> str:
    return (
        "<div class='rcb rcb-empty'><div class='big'>No submissions yet</div>"
        "<p>Be the first: see <a href='#' data-tab='Take part'><b>Take part</b></a> "
        "to download the 10 scans, run your agent and upload a zip.</p></div>"
    )


def component_means(row: dict, task_ids: list[str]) -> dict[str, float] | None:
    """Mean S1..S4 over all task ids (missing task = 0), from the public per-task scores."""
    per = row.get("per_task")
    if not isinstance(per, dict) or not per:
        return None
    ids = list(task_ids) or list(per)
    out = {}
    for comp, _ in COMPONENTS:
        vals = [_num((per.get(t) or {}).get(comp)) or 0.0 for t in ids]
        out[comp] = sum(vals) / len(ids) if ids else 0.0
    return out


def _row_badges(r: dict) -> str:
    out = []
    if r.get("status") == "done":
        if r.get("reference"):
            out.append("<span class='rcb-badge reference' title='live-broadcast station'>"
                       "reference</span>")  # fmt: skip
        if r.get("validated"):
            out.append("<span class='rcb-badge validated' title='rebuilt from the native model "
                       "by an admin'>validated</span>")  # fmt: skip
        else:
            out.append("<span class='rcb-badge unvalidated' title='automatic Open score "
                       "only'>unvalidated</span>")  # fmt: skip
    else:
        st = STATUS_LABELS.get(str(r.get("status")), "")
        if st:
            out.append(f"<span class='rcb-badge {st}'>{st}</span>")
    if r.get("human_in_loop"):
        out.append("<span class='rcb-badge hil' title='a person edited geometry or steered the "
                   "agent'>human in loop</span>")  # fmt: skip
    return "".join(out)


def leaderboard_html(
    rows: list[dict], task_ids: list[str] | None = None, report_base: str = "reports"
) -> str:
    """The styled Open /95 table (HTML). Empty board -> an empty-state card."""
    task_ids = list(task_ids or [])
    shown = board_rows(rows)
    if not shown:
        return empty_state_html()
    n_tasks = len(task_ids)
    comp_heads = "".join(
        f"<th class='num' title='mean {c} over all tasks'>{c} <span class='muted'>/{m}</span></th>"
        for c, m in COMPONENTS
    )
    head = (
        "<tr><th>#</th><th>Submission</th><th>Model &middot; CAD backend</th>"
        f"<th class='num'>Open /95</th>{comp_heads}<th class='num'>Solved</th>"
        "<th class='num'>Gate OK</th><th>Date</th><th></th></tr>"
    )
    body = []
    rank = 0
    for r in shown:
        done = r.get("status") == "done"
        if done:
            rank += 1
            medal = f" r{rank}" if rank <= 3 else ""
            rank_html = f"<span class='rcb-rank{medal}'>{rank}</span>"
        else:
            rank_html = ""
        sid = str(r.get("submission_id", ""))
        name = _text(r.get("submission_name") or sid)
        url = safe_url(r.get("agent_url"))
        if url:
            name = (
                f"<a href='{esc(url)}' target='_blank' rel='nofollow noopener noreferrer ugc'>"
                f"{name}</a>"
            )
        by = []
        if r.get("submitter_name"):
            by.append(f"by {_text(r.get('submitter_name'))}")
        if r.get("harness"):
            by.append(f"harness: {_text(r.get('harness'))}")
        by_html = f"<div class='rcb-by'>{' &middot; '.join(by)}</div>" if by else ""
        total = _num(r.get("open_total")) if done else None
        if total is not None:
            pct = max(0.0, min(100.0, 100.0 * total / OPEN_MAX))
            score = (
                f"<div class='rcb-score'><div class='rcb-bar'><span style='width:{pct:.1f}%'>"
                f"</span></div><b>{total:.1f}</b></div>"
            )
        else:
            score = "<span class='muted'>-</span>"
        means = component_means(r, task_ids) if done else None
        comps = "".join(
            f"<td class='num'>{means[c]:.1f}</td>" if means else "<td class='num muted'>-</td>"
            for c, _ in COMPONENTS
        )
        of = f"<span class='muted'>/{n_tasks}</span>" if n_tasks else ""
        solved = f"{_text(r.get('n_solved', '-'), 6)}{of}" if done else "-"
        gate = f"{_text(r.get('n_gate_ok', '-'), 6)}{of}" if done else "-"
        href = _report_href(r, report_base)
        report = (
            f"<a class='rcb-report' href='{esc(href)}' target='_blank' rel='noopener'>Report</a>"
            if href
            else ""
        )
        body.append(
            f"<tr class='{'' if done else 'unfinished'}'><td>{rank_html}</td>"
            f"<td class='subm'><div class='rcb-name'>{name}</div>{by_html}"
            f"<div class='rcb-badges'>{_row_badges(r)}</div></td>"
            f"<td><div class='rcb-model'>{_text(r.get('model')) or '-'}</div>"
            f"<div class='rcb-by'>{_text(r.get('cad_backend'))}</div></td>"
            f"<td class='num'>{score}</td>{comps}"
            f"<td class='num'>{solved}</td><td class='num'>{gate}</td>"
            f"<td class='muted' style='white-space:nowrap'>{_text(r.get('submitted_at'), 10)}</td>"
            f"<td>{report}</td></tr>"
        )
    return (
        "<div class='rcb'><div class='rcb-board-wrap'><table class='rcb-board'>"
        f"<thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div></div>"
    )


def my_submissions_html(rows: list[dict], user: str | None, report_base: str = "reports") -> str:
    """The signed-in user's own submissions, newest first."""
    if not user:
        return "<div class='rcb rcb-note'>Sign in to see your submissions.</div>"
    mine = [r for r in rows if r.get("hf_user") == user]
    if not mine:
        return "<div class='rcb rcb-note'>No submissions yet.</div>"
    place = {r.get("submission_id"): i for i, r in enumerate(waiting_rows(rows), 1)}
    body = []
    for r in reversed(mine):
        st = STATUS_LABELS.get(str(r.get("status", "")), "")
        status = f"<span class='rcb-badge {st}'>{st}</span>" if st else ""
        if r.get("status_message"):
            status += f" <span class='muted'>{_text(r['status_message'])}</span>"
        elif r.get("submission_id") in place:
            status += f" <span class='muted'>#{place[r.get('submission_id')]} in line</span>"
        done = r.get("status") == "done"
        href = _report_href(r, report_base)
        report = (
            f"<a class='rcb-report' href='{esc(href)}' target='_blank' rel='noopener'>Report</a>"
            if href
            else ""
        )
        body.append(
            f"<tr><td><div class='rcb-name'>{_text(r.get('submission_name'))}</div>"
            f"<div class='rcb-by'><code>{_text(r.get('submission_id'))}</code></div></td>"
            f"<td>{status}</td>"
            f"<td class='num'>{_fmt(r.get('open_total')) if done else '-'}</td>"
            f"<td class='muted'>{_text(r.get('submitted_at'), 20)}</td><td>{report}</td></tr>"
        )
    return (
        "<div class='rcb'><div class='rcb-board-wrap'><table class='rcb-board rcb-mine'>"
        "<thead><tr><th>Submission</th><th>Status</th><th class='num'>Open /95</th>"
        "<th>Submitted (UTC)</th><th></th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></div>"
    )


# --- heatmap ----------------------------------------------------------------------------------


def _cell_color(total: float | None) -> str:
    if total is None:
        return "transparent"
    t = max(0.0, min(1.0, total / 95.0))
    # light red -> amber -> green, kept light so dark text stays readable
    hue = int(120 * t)
    return f"hsl({hue}, 65%, 78%)"


def heatmap_html(rows: list[dict], task_ids: list[str], tasks: list[dict] | None = None) -> str:
    """Task x submission table of per-task Open totals (scored rows only), coloured cells."""
    done = [r for r in board_rows(rows, include_unfinished=False)]
    if not done:
        return "<p>No scored submissions yet. Scored runs appear here task by task.</p>"
    info = {str(t.get("id")): t for t in tasks or [] if isinstance(t, dict)}
    heads = []
    for i, t in enumerate(task_ids):
        meta = info.get(t, {})
        title = task_title(meta or {"id": t})
        no = task_number(meta, i)
        heads.append(
            f"<th title='{esc(title)} ({esc(t)})'>{no:02d}<br>"
            f"<span style='font-weight:500'>{esc(_short(t))}</span></th>"
        )
    body = []
    for r in done:
        per = r.get("per_task") or {}
        cells = []
        for t in task_ids:
            e = per.get(t) or {}
            st = str(e.get("status", "missing"))
            total = _num(e.get("total"))
            label = _fmt(total) if st == "ok" or total is not None else st.replace("_", " ")
            cls = "cell" if total is not None else "cell none"
            cells.append(
                f"<td class='{cls}' style='background:{_cell_color(total)}' "
                f"title='{esc(st)}'>{esc(label)}</td>"
            )
        name = _text(r.get("submission_name") or r.get("submission_id"))
        flag = " <span class='rcb-badge reference'>ref</span>" if r.get("reference") else ""
        body.append(
            f"<tr><td class='sub'>{name}{flag}</td><td class='tot'>"
            f"{_fmt(r.get('open_total'))}</td>{''.join(cells)}</tr>"
        )
    return (
        "<div class='rcb'><div class='rcb-heat-wrap'><table class='rcb-heat'>"
        f"<thead><tr><th class='sub'>Submission</th><th>Open /95</th>{''.join(heads)}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></div>"
    )


def _short(task_id: str) -> str:
    """'r385_hs01_motor_mount' -> 'hs01', 'od_h11_thermoblock' -> 'h11'."""
    parts = task_id.split("_")
    return parts[1] if len(parts) > 1 else task_id


# --- tasks ------------------------------------------------------------------------------------

# Only these task fields are ever shown (no dimensions, no GT).
TASK_FIELDS = (
    "id",
    "no",
    "title",
    "family",
    "difficulty",
    "tolerance_class",
    "tier",
    "triangles",
    "known_defects",
)
# Public product families by task-id prefix (README "The 10 tasks"); used when a row has none.
FAMILY_BY_PREFIX = {"r385": "R385 diaphragm pump", "od": "Open Dedica espresso machine"}
DIFFICULTIES = ("medium", "hard", "very hard")


def _read_task_rows(path: str | os.PathLike | None) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    if not path or not Path(path).is_file():
        return by_id
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return by_id
    for t in data if isinstance(data, list) else []:
        if isinstance(t, dict) and isinstance(t.get("id"), str):
            by_id[t["id"]] = {k: t[k] for k in TASK_FIELDS if k in t}
    return by_id


def load_tasks(
    path: str | os.PathLike | None,
    task_ids: list[str],
    public_path: str | os.PathLike | None = None,
) -> list[dict]:
    """Task cards from the inputs dataset's tasks.json, restricted to TASK_FIELDS.

    ``public_path`` (``docs/benchmark/tasks_public.json``: no, title, family, difficulty) is a
    base layer; tasks.json fields win where both have a value.
    """
    by_id = _read_task_rows(public_path)
    for tid, row in _read_task_rows(path).items():
        by_id[tid] = {**by_id.get(tid, {}), **row}
    ids = task_ids or list(by_id)
    return [by_id.get(t, {"id": t}) for t in ids]


def order_tasks(tasks: list[dict]) -> list[dict]:
    """Display order: by task ``no`` where present, else keep the given (task-id) order."""
    indexed = list(enumerate(tasks))
    return [t for _, t in sorted(indexed, key=lambda it: (task_number(it[1], it[0]), it[0]))]


def humanize_task_id(task_id: str) -> str:
    """'r385_hs01_motor_mount' -> 'Motor mount' (fallback title)."""
    parts = task_id.split("_")
    words = parts[2:] if len(parts) > 2 else parts[-1:]
    text = " ".join(words).strip()
    return (text[:1].upper() + text[1:]) if text else task_id


def task_title(task: dict) -> str:
    title = task.get("title")
    return (
        str(title)
        if isinstance(title, str) and title.strip()
        else humanize_task_id(str(task.get("id", "")))
    )


def task_family(task: dict) -> str:
    fam = task.get("family")
    if isinstance(fam, str) and fam.strip():
        return fam
    return FAMILY_BY_PREFIX.get(str(task.get("id", "")).split("_", 1)[0], "")


def task_number(task: dict, index: int) -> int:
    no = task.get("no")
    return no if isinstance(no, int) and not isinstance(no, bool) and no > 0 else index + 1


def _compact_int(v: Any) -> str:
    x = _num(v)
    if x is None:
        return esc(str(v))
    if x >= 1e6:
        return f"{x / 1e6:.2f}M"
    if x >= 1e3:
        return f"{x / 1e3:.0f}k"
    return f"{x:.0f}"


def task_card_html(
    task: dict,
    inputs_repo: str | None,
    preview_url: str | None = None,
    *,
    fallback_url: str | None = None,
    index: int = 0,
) -> str:
    """One task card: preview, number + title, family, difficulty, class, tier, triangles,
    known defects (collapsible) and downloads. Shows only TASK_FIELDS (no dimensions, no GT)."""
    tid = str(task.get("id", ""))
    no = task_number(task, index)
    family = task_family(task)
    fam_color = "var(--rcb-cyan)" if family.lower().startswith("r385") else "var(--rcb-orange)"
    diff = str(task.get("difficulty") or "").strip().lower()
    diff_pill = (
        f"<span class='rcb-pill d-{esc(diff.replace(' ', '-'))}'>{esc(diff)}</span>" if diff else ""
    )

    if preview_url:
        onerr = "this.style.display='none'"
        if fallback_url and TASK_ID_RE.match(tid) and fallback_url == f"/assets/tasks/{tid}.webp":
            onerr = f"this.onerror=null;this.src='{fallback_url}'"
        img = (
            f"<img src='{esc(preview_url)}' alt='{esc(task_title(task))}: raw scan render' "
            f"loading='lazy' onerror=\"{esc(onerr)}\">"
        )
    else:
        img = f"<span class='ph'>{no:02d}</span>"

    facts = []
    if "tolerance_class" in task:
        facts.append(("Class", esc(str(task["tolerance_class"])), "tolerance class"))
    if "tier" in task:
        facts.append(("Tier", esc(str(task["tier"])), "task tier"))
    if "triangles" in task:
        tri = task["triangles"]
        full = f"{int(tri):,}" if _num(tri) is not None else str(tri)
        facts.append(("Triangles", _compact_int(tri), f"{full} scan triangles"))
    facts_html = "".join(
        f"<div class='fact' title='{esc(t)}'><div class='k'>{k}</div><div class='v'>{v}</div></div>"
        for k, v, t in facts
    )

    defects = task.get("known_defects") or []
    if isinstance(defects, str):
        defects = [defects]
    items = [f"<li>{esc(str(d))}</li>" for d in defects if d] if isinstance(defects, list) else []
    defects_html = (
        f"<details><summary>Known scan defects ({len(items)})</summary><ul>{''.join(items)}</ul>"
        "</details>"
        if items
        else ""
    )

    links = ""
    if inputs_repo:
        base = f"https://huggingface.co/datasets/{inputs_repo}/resolve/main/parts/{tid}"
        links = (
            f"<div class='dl'><a class='primary' href='{esc(base)}/input.stl?download=true' "
            f"rel='noopener'>input.stl</a>"
            f"<a href='{esc(base)}/task.yaml' target='_blank' rel='noopener'>task.yaml</a></div>"
        )
    return (
        "<div class='rcb-card'>"
        f"<div class='media'>{img}<span class='no'>{no:02d}</span>{diff_pill}</div>"
        "<div class='body'>"
        f"<div class='title'>{esc(task_title(task))}</div>"
        f"<div class='tid'>{esc(tid)}</div>"
        + (f"<div class='fam' style='--fam:{fam_color}'>{esc(family)}</div>" if family else "")
        + (f"<div class='facts'>{facts_html}</div>" if facts_html else "")
        + defects_html
        + links
        + "</div></div>"
    )


def tasks_grid_html(
    tasks: list[dict],
    inputs_repo: str | None,
    previews: dict[str, tuple[str | None, str | None]] | None = None,
) -> str:
    """Responsive card grid; ``previews`` maps task id -> (preview url, fallback url)."""
    previews = previews or {}
    cards = []
    for i, t in enumerate(tasks):
        url, fallback = previews.get(str(t.get("id", "")), (None, None))
        cards.append(task_card_html(t, inputs_repo, url, fallback_url=fallback, index=i))
    return f"<div class='rcb rcb-grid'>{''.join(cards)}</div>"
