"""Markdown / CSV report over result.json files (one row per run)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

COLUMNS = [
    ("part", lambda r: r["part_id"]),
    ("run", lambda r: r["run_id"]),
    ("gate", lambda r: "ok" if r["gate"]["ok"] else "FAIL"),
    ("S1", lambda r: r["scores"].get("S1")),
    ("S2", lambda r: r["scores"].get("S2")),
    ("S3", lambda r: r["scores"].get("S3")),
    ("S4", lambda r: r["scores"].get("S4")),
    ("S5", lambda r: r["scores"].get("S5")),
    ("total", lambda r: r["scores"].get("total")),
    ("capped", lambda r: r["scores"].get("capped")),
    ("PASS", lambda r: r["pass"]),
    ("p_tight", lambda r: (r.get("metrics") or {}).get("p_tight")),
    ("p_wide", lambda r: (r.get("metrics") or {}).get("p_wide")),
    ("p_tight_inf", lambda r: (r.get("metrics") or {}).get("p_tight_inferred")),
    ("p_wide_inf", lambda r: (r.get("metrics") or {}).get("p_wide_inferred")),
    ("band_tight_eff", lambda r: (r.get("metrics") or {}).get("band_tight_eff_mm")),
    ("band_wide_eff", lambda r: (r.get("metrics") or {}).get("band_wide_eff_mm")),
    ("r_feat", lambda r: (r.get("metrics") or {}).get("r_feat")),
    ("key_dims_ok", lambda r: (r.get("metrics") or {}).get("key_dims_ok")),
    ("plan", lambda r: r["scores"].get("plan_score")),
    ("gate_reason", lambda r: r["gate"].get("reason") or ""),
]


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 1.5 else f"{v:.1f}"
    return str(v)


def rows(results: list[dict]) -> list[list[str]]:
    return [[_fmt(f(r)) for _, f in COLUMNS] for r in results]


def markdown(results: list[dict], title: str | None = None) -> str:
    head = [c for c, _ in COLUMNS]
    out = [f"## {title}", ""] if title else []
    out.append("| " + " | ".join(head) + " |")
    out.append("|" + "---|" * len(head))
    out.extend("| " + " | ".join(r) + " |" for r in rows(results))
    return "\n".join(out) + "\n"


def to_csv(results: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c for c, _ in COLUMNS])
    w.writerows(rows(results))
    return buf.getvalue()


def load(paths: list[str | Path]) -> list[dict]:
    files: list[Path] = []
    for p in map(Path, paths):
        files.extend(sorted(p.rglob("result.json")) if p.is_dir() else [p])
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]
