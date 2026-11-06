"""Expand an experiment YAML into run jobs (cartesian product under the D4 track rules).

    recadbench-orch expand experiments/pilot.yaml --count
    recadbench-orch expand experiments/pilot.yaml --jobs jobs.json
    recadbench-orch expand experiments/pilot.yaml --enqueue orchestrator.db   # evaluator host

Experiment format::

    name: pilot                       # run-id prefix
    parts: [r385_hs01_motor_mount]    # or  split: dev  (reads <data>/splits/dev.txt)
    replicates: 1
    harness_level: H3                 # optional (compose default)
    budgets: {max_turns: 200}         # optional, override task.yaml
    refs: {strategy: origin/main}     # optional git refs (skillset default: tag <b>-vN)
    models:
      - {id: claude-sonnet-5-5, provider: anthropic, agent_backend: claude_agent_sdk}
    tracks:
      T: {strategies: [oracle], skillsets: [build123d@v1, none@build123d]}
      E: {strategies: [B, D], skillsets: [build123d@v1]}
      S: {strategies: [B]}            # plan only: skillset none, backend none
    exclude:                          # optional: drop combinations matching all given keys
      - {track: E, strategy: D, skillset: nx@v1}

Rules (00_DECISIONS D4, run_manifest schema): Track T = strategy ``oracle`` on a CAD backend,
dev parts only; Track S = plan only (no skillset / backend); Track E = a strategy other than
oracle on a CAD backend. A track block that breaks them is an error, not silently skipped.
Skillsets: ``<backend>@vN`` or ``none@<backend>`` (bare-L1 baseline).
Track T jobs need the scanner-frame seed plan in their payload; ``--enqueue`` makes it on the
evaluator host (``recadbench_run.seed``, reads RECADBENCH_GT_DIR); ``--jobs`` leaves
``seed_plan: null`` for Track T unless ``--seed-plans`` is given.
"""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter
from pathlib import Path

import yaml

TRACKS = ("T", "S", "E")
SKILLSET_RE = re.compile(r"^(?:(?P<b>[a-z0-9]+)@v[0-9]+|none@(?P<nb>[a-z0-9]+))$")


class ExpandError(ValueError):
    pass


def _skillset(s: str) -> tuple[str, str]:
    """``(skillset id for compose, backend)``."""
    m = SKILLSET_RE.match(s)
    if not m:
        raise ExpandError(f"skillset {s!r}: use '<backend>@vN' or 'none@<backend>'")
    return ("none", m.group("nb")) if m.group("nb") else (s, m.group("b"))


def _parts(exp: dict, data_dir: Path | None) -> list[str]:
    if exp.get("parts"):
        return list(exp["parts"])
    split = exp.get("split")
    if not split:
        raise ExpandError("give parts: [...] or split: dev")
    if data_dir is None:
        raise ExpandError("split: needs the data dir (--data-dir / RECADBENCH_DATA_DIR)")
    return (data_dir / "splits" / f"{split}.txt").read_text(encoding="utf-8").split()


def _dev_parts(data_dir: Path | None) -> set[str] | None:
    if data_dir is None:
        return None
    p = data_dir / "splits" / "dev.txt"
    return set(p.read_text(encoding="utf-8").split()) if p.is_file() else None


def run_id(name: str, track: str, strategy: str, skillset: str, backend: str, model: str,
           part: str, rep: int) -> str:
    sk = skillset.replace("@", "-") if skillset != "none" else f"none-{backend}"
    raw = f"{name}_{track}_{strategy}_{sk}_{model}_{part}_r{rep}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def expand(exp: dict, data_dir: Path | None = None) -> list[dict]:
    name = exp.get("name") or "exp"
    parts = _parts(exp, data_dir)
    dev = _dev_parts(data_dir)
    models = exp.get("models") or []
    if not models:
        raise ExpandError("models: at least one model")
    reps = int(exp.get("replicates", 1))
    tracks = exp.get("tracks") or {}
    excl = exp.get("exclude") or []
    jobs: list[dict] = []
    for track, block in tracks.items():
        if track not in TRACKS:
            raise ExpandError(f"unknown track {track!r}")
        strategies = block.get("strategies") or []
        skillsets = block.get("skillsets") or (["none@none"] if track == "S" else [])
        if not strategies or not skillsets:
            raise ExpandError(f"track {track}: strategies and skillsets are required")
        for s in strategies:
            if track == "T" and s != "oracle":
                raise ExpandError(f"track T runs only strategy oracle (got {s})")
            if track != "T" and s == "oracle":
                raise ExpandError(f"strategy oracle runs only in track T (got track {track})")
        combos = []
        for sk in skillsets:
            sid, backend = ("none", "none") if sk == "none@none" else _skillset(sk)
            if track == "S" and backend != "none":
                raise ExpandError("track S is plan-only: no skillsets")
            if track != "S" and backend == "none":
                raise ExpandError(f"track {track} needs a CAD backend")
            combos.append((sk, sid, backend))
        for s, (sk, sid, backend), mdl, part, rep in itertools.product(
                strategies, combos, models, parts, range(reps)):
            if track == "T" and dev is not None and part not in dev:
                raise ExpandError(f"track T is dev-only: {part} is not in splits/dev.txt")
            row = {"track": track, "strategy": s, "skillset": sk, "model": mdl["id"],
                   "part": part, "replicate": rep, "backend": backend}
            if any(all(row.get(k) == v for k, v in e.items()) for e in excl):
                continue
            rid = run_id(name, track, s, sid, backend, mdl["id"], part, rep)
            payload = {"run_id": rid, "track": track, "strategy": s, "skillset": sid,
                       "backend": backend, "part_id": part, "replicate": rep,
                       "model": mdl["id"], "provider": mdl.get("provider", "anthropic"),
                       "agent_backend": mdl.get("agent_backend", "claude_agent_sdk"),
                       "harness_level": exp.get("harness_level") or "H3",
                       "budgets": dict(exp.get("budgets") or {}),
                       "strategy_ref": (exp.get("refs") or {}).get("strategy"),
                       "skillset_ref": (exp.get("refs") or {}).get("skillset"),
                       "seed_plan": None}
            if mdl.get("agent_options"):
                payload["agent_options"] = mdl["agent_options"]
            jobs.append({"kind": "run", "run_id": rid, "backend": None if backend == "none"
                         else backend, "requires": [] if backend == "none" else [backend],
                         "payload": payload})
    ids = [j["run_id"] for j in jobs]
    dup = [k for k, n in Counter(ids).items() if n > 1]
    if dup:
        raise ExpandError(f"duplicate run ids: {dup[:3]}")
    return jobs


def summary(jobs: list[dict]) -> dict:
    by = lambda key: dict(sorted(Counter(key(j) for j in jobs).items()))  # noqa: E731
    return {"runs": len(jobs),
            "by_track": by(lambda j: j["payload"]["track"]),
            "by_backend": by(lambda j: j["payload"]["backend"]),
            "by_model": by(lambda j: j["payload"]["model"]),
            "by_strategy": by(lambda j: j["payload"]["strategy"])}


def attach_seed_plans(jobs: list[dict], seed_fn=None) -> list[str]:
    """Fill ``seed_plan`` of the Track T jobs (evaluator host). Returns the mapping notes."""
    if seed_fn is None:
        from recadbench_run.seed import seed_plan_for as seed_fn
    notes, cache = [], {}
    for j in jobs:
        p = j["payload"]
        if p["track"] != "T":
            continue
        if p["part_id"] not in cache:
            cache[p["part_id"]] = seed_fn(p["part_id"])
        plan, n = cache[p["part_id"]]
        p["seed_plan"] = plan
        notes += [f"{p['part_id']}: {x}" for x in n]
    return sorted(set(notes))


def load(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def enqueue(jobs: list[dict], queue) -> list[int]:
    missing = [j["run_id"] for j in jobs if j["payload"]["track"] == "T"
               and not j["payload"].get("seed_plan")]
    if missing:
        raise ExpandError(f"Track T jobs without a seed plan: {missing[:3]}")
    return [queue.enqueue("run", j["payload"], requires=j["requires"], backend=j["backend"],
                          run_id=j["run_id"]) for j in jobs]


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"
