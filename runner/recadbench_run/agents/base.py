"""Common agent-backend interface."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..budgets import Budgets, BudgetTracker

TASK_PROMPT = ("Work on the ReCADBench task described in CLAUDE.md in this folder. Follow the "
               "strategy skill named there, deliver the files it lists, then stop. Subagents "
               "run in the foreground: wait for each one's result before you continue; the "
               "session ends when you stop. When you are done, reply with one line: DONE or "
               "GAVE_UP and the reason.")


@dataclass
class AgentResult:
    ok: bool
    stop_reason: str
    efficiency: dict
    transcript: str | None = None
    details: dict = field(default_factory=dict)


def run_budgets(run_dir: Path) -> Budgets:
    m = json.loads((Path(run_dir) / "run_manifest.json").read_text(encoding="utf-8"))
    return Budgets.from_manifest(m)


def manifest(run_dir: Path) -> dict:
    return json.loads((Path(run_dir) / "run_manifest.json").read_text(encoding="utf-8"))


def new_tracker(run_dir: Path, budgets: Budgets | None = None) -> BudgetTracker:
    return BudgetTracker(budgets or run_budgets(run_dir)).start()


class TranscriptWriter:
    """``.recadbench/transcript.jsonl``: one JSON object per event (model turn, tool call)."""

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / ".recadbench" / "transcript.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a", encoding="utf-8", newline="\n")

    def write(self, **event) -> None:
        self.fh.write(json.dumps(event, default=str, ensure_ascii=True) + "\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()
