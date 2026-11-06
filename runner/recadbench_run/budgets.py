"""Run budgets (00_DECISIONS D5: the manifest overrides task.yaml) and their enforcement.

Keys (run_manifest ``budgets``): ``max_wall_s``, ``max_turns``, ``max_tokens`` (input + output,
cache reads excluded), ``max_cost_usd``. :class:`BudgetTracker` is fed by every agent backend
(turns and token usage per model response) and answers ``exceeded()`` with the first budget
that ran out; the backends stop the session there and record it as ``stop_reason``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

KEYS = ("max_wall_s", "max_turns", "max_tokens", "max_cost_usd")


@dataclass
class Budgets:
    max_wall_s: float | None = None
    max_turns: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None

    @classmethod
    def merge(cls, task_budgets: dict | None, overrides: dict | None) -> Budgets:
        vals: dict = {}
        for src in (task_budgets or {}, overrides or {}):
            for k, v in src.items():
                if k not in KEYS:
                    raise ValueError(f"unknown budget {k!r} (known: {', '.join(KEYS)})")
                if v is not None:
                    if v <= 0:
                        raise ValueError(f"budget {k} must be positive")
                    vals[k] = v
        return cls(**vals)

    @classmethod
    def from_manifest(cls, manifest: dict) -> Budgets:
        return cls.merge(manifest.get("budgets") or {}, None)

    def to_manifest(self) -> dict:
        out = {}
        for k in KEYS:
            v = getattr(self, k)
            if v is not None:
                out[k] = int(v) if k in ("max_wall_s", "max_turns", "max_tokens") else float(v)
        return out


@dataclass
class BudgetTracker:
    budgets: Budgets
    clock: object = time.monotonic
    t0: float | None = field(default=None)
    turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_errors: int = 0
    gate_denials: int = 0

    def start(self) -> BudgetTracker:
        self.t0 = self.clock()
        return self

    @property
    def wall_s(self) -> float:
        return self.clock() - self.t0 if self.t0 is not None else 0.0

    def add_turn(self, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0) -> None:
        self.turns += 1
        self.tokens_in += int(tokens_in or 0)
        self.tokens_out += int(tokens_out or 0)
        self.cost_usd += float(cost_usd or 0.0)

    def exceeded(self) -> str | None:
        b = self.budgets
        if b.max_turns is not None and self.turns >= b.max_turns:
            return f"max_turns ({b.max_turns})"
        if b.max_tokens is not None and self.tokens_in + self.tokens_out >= b.max_tokens:
            return f"max_tokens ({b.max_tokens})"
        if b.max_cost_usd is not None and self.cost_usd >= b.max_cost_usd:
            return f"max_cost_usd ({b.max_cost_usd})"
        if b.max_wall_s is not None and self.wall_s >= b.max_wall_s:
            return f"max_wall_s ({b.max_wall_s})"
        return None

    def efficiency(self, stop_reason: str | None = None) -> dict:
        """efficiency.json (result schema ``efficiency`` + two informational keys)."""
        out = {"tokens_in": int(self.tokens_in), "tokens_out": int(self.tokens_out),
               "cost_usd": round(float(self.cost_usd), 6), "wall_s": round(self.wall_s, 3),
               "turns": int(self.turns), "tool_errors": int(self.tool_errors),
               "gate_denials": int(self.gate_denials)}
        if stop_reason:
            out["stop_reason"] = stop_reason
        return out
