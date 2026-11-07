"""Cost / wall-time estimate for an expanded experiment (H9; numbers in PILOT_ESTIMATE.md).

    python orchestrator/experiments/pilot_estimate.py orchestrator/experiments/pilot.yaml

A model, not a measurement: every per-run profile below is an assumption anchored on the H8
evidence (see PILOT_ESTIMATE.md). Cost per run follows the Claude Code cache pattern seen in
the H8 smoke run (1-hour cache writes, every turn re-reads the cached context):

    turn t (1..T): read  C0 + g*(t-1)   at the cache-hit price
                   write g (C0 on turn 1) at the 1-hour cache-write price
                   out   o               at the output price (thinking included)

times a subagent factor m for skillsets that ship subagents. Wall time per run = T x seconds
per turn + CAD executions x per-execution overhead + finish; the score job is separate (ASUS,
needs gt). Makespan: list scheduling with one NX lane (00_DECISIONS D9) and N other lanes.
"""

from __future__ import annotations

import heapq
import sys
from pathlib import Path

from recadbench_orch.expand import expand, load

# USD per million tokens, platform.claude.com/docs/en/about-claude/pricing (read 2026-11-07)
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "w5m": 5.00, "w1h": 8.00, "read": 0.20, "out": 20.00},
    "claude-sonnet-5-5": {"in": 2.00, "w5m": 2.50, "w1h": 4.00, "read": 0.10, "out": 10.00},
}

# initial cached context C0 (tokens): build123d@v1 measured in the H8 smoke (37.9k); others assumed
C0 = {"build123d@v1": 38_000, "nx@v1": 45_000, "nx@v2": 47_000,
      "none@build123d": 30_000, "none@nx": 30_000,
      "none@none": 33_000}

# per run class: turns T, context growth g / turn, output o / turn, subagent factor m,
# CAD executions x (count of execute_file / nx-run calls)
SCEN = {
    "low": {
        "T": {"T": 15, "g": 2000, "o": 800, "m": 1.0, "x": 2},
        "E": {"T": 40, "g": 2500, "o": 1000, "m": 1.0, "x": 4},
        "S": {"T": 15, "g": 2000, "o": 800, "m": 1.0, "x": 0},
    },
    "expected": {
        "T": {"T": 40, "g": 3000, "o": 1500, "m": 1.3, "x": 4},
        "E": {"T": 100, "g": 3000, "o": 1500, "m": 1.3, "x": 10},
        "S": {"T": 35, "g": 2500, "o": 1200, "m": 1.0, "x": 0},
    },
    # the budget ceiling: max_turns 200 (task.yaml) reached, wall = max_wall_s 3600
    "cap": {
        "T": {"T": 200, "g": 3000, "o": 2000, "m": 1.5, "x": 20},
        "E": {"T": 200, "g": 3000, "o": 2000, "m": 1.5, "x": 30},
        "S": {"T": 200, "g": 3000, "o": 2000, "m": 1.0, "x": 0},
    },
}
SEC_PER_TURN = {"low": {"claude-sonnet-5-5": 6, "claude-opus-5-5": 8},
                "expected": {"claude-sonnet-5-5": 12, "claude-opus-5-5": 18},
                "cap": {"claude-sonnet-5-5": None, "claude-opus-5-5": None}}
MAX_WALL_S = 3600
EXEC_S = {"build123d": 3, "nx": 25, "none": 0}      # per CAD execution (NX: run_journal start)
FINISH_S = {"build123d": 30, "nx": 60, "none": 5}   # export + extract (H8 replay / nx_smoke)
# score job seconds per scenario. H9 replay: hs01 oracle run + finish + score 100 s in all;
# the NX smoke (wrong geometry, alignment falls back to the PCA search) scored in about 450 s
SCORE_S = {"low": {"geom": 90, "plan": 30}, "expected": {"geom": 210, "plan": 30},
           "cap": {"geom": 450, "plan": 60}}
PHONE_SLOWDOWN = 1.5                                # CPU-bound parts on the phone (untested)


def run_cost(model: str, c0: int, p: dict) -> float:
    pr, t, g, o = PRICES[model], p["T"], p["g"], p["o"]
    reads = sum(c0 + g * (k - 1) for k in range(2, t + 1))
    writes = c0 + g * (t - 1)
    usd = (reads * pr["read"] + writes * pr["w1h"] + t * o * pr["out"]) / 1e6
    return usd * p["m"]


def run_wall(job: dict, scen: str) -> tuple[float, float]:
    """(agent + finish seconds, of which CPU-bound CAD seconds)."""
    pl = job["payload"]
    p = SCEN[scen][pl["track"]]
    b = pl["backend"]
    cad = p["x"] * EXEC_S[b]
    spt = SEC_PER_TURN[scen][pl["model"]]
    agent = MAX_WALL_S if spt is None else min(MAX_WALL_S, p["T"] * spt + cad)
    return agent + FINISH_S[b], cad + FINISH_S[b]


def subagent_free(job: dict) -> bool:
    return job["payload"]["skillset"] == "none"


def profile(job: dict, scen: str) -> dict:
    p = dict(SCEN[scen][job["payload"]["track"]])
    if subagent_free(job):
        p["m"] = 1.0
    return p


def costs(jobs: list[dict], scen: str) -> list[float]:
    out = []
    for j in jobs:
        pl = j["payload"]
        sk = pl["skillset"] if pl["skillset"] != "none" else f"none@{pl['backend']}"
        out.append(run_cost(pl["model"], C0[sk], profile(j, scen)))
    return out


def makespan(jobs: list[dict], scen: str, lanes: int, phone: bool = False) -> float:
    """One NX lane + `lanes` ASUS lanes (+ one phone lane for build123d / plan-only runs).
    Score jobs run on ASUS lanes after their run. Longest runs first."""
    nx = sorted((run_wall(j, scen)[0] for j in jobs if j["payload"]["backend"] == "nx"),
                reverse=True)
    other = sorted(((run_wall(j, scen), j) for j in jobs if j["payload"]["backend"] != "nx"),
                   key=lambda r: -r[0][0])
    t_nx, nx_done = 0.0, []
    for w in nx:
        t_nx += w
        nx_done.append(t_nx)
    asus = [0.0] * lanes
    ph = [0.0] if phone else []
    done = []
    for (w, cpu), _j in other:
        a = min(asus)
        if phone and ph[0] + w + cpu * (PHONE_SLOWDOWN - 1) < a + w:
            ph[0] += w + cpu * (PHONE_SLOWDOWN - 1)
            done.append((ph[0], "geom" if _j["payload"]["backend"] != "none" else "plan"))
        else:
            i = asus.index(a)
            asus[i] += w
            done.append((asus[i], "geom" if _j["payload"]["backend"] != "none" else "plan"))
    done += [(t, "geom") for t in nx_done]
    heap = list(asus)
    heapq.heapify(heap)
    end = max([t_nx] + asus + ph)
    for t_ready, kind in sorted(done):
        lane = heapq.heappop(heap)
        fin = max(lane, t_ready) + SCORE_S[scen][kind]
        heapq.heappush(heap, fin)
        end = max(end, fin)
    return end


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "orchestrator/experiments/pilot.yaml")
    jobs = expand(load(path))
    models = sorted({j["payload"]["model"] for j in jobs})
    print(f"runs: {len(jobs)}\n")
    print("| scenario | " + " | ".join(models) + " | total |")
    print("|---|" + "---:|" * (len(models) + 1))
    for scen in SCEN:
        cs = costs(jobs, scen)
        per = {m: sum(c for c, j in zip(cs, jobs, strict=True) if j["payload"]["model"] == m)
               for m in models}
        print(f"| {scen} | " + " | ".join(f"${per[m]:,.0f}" for m in models)
              + f" | ${sum(cs):,.0f} |")
    print("\nper-run cost (USD) by track and model:\n")
    print("| scenario | track | skillset kind | " + " | ".join(models) + " |")
    print("|---|---|---|" + "---:|" * len(models))
    for scen in SCEN:
        seen = {}
        for c, j in zip(costs(jobs, scen), jobs, strict=True):
            pl = j["payload"]
            kind = "none" if pl["skillset"] == "none" else pl["skillset"]
            seen.setdefault((pl["track"], kind), {})[pl["model"]] = c
        for (tr, kind), d in sorted(seen.items()):
            print(f"| {scen} | {tr} | {kind} | " + " | ".join(f"{d[m]:.2f}" for m in models)
                  + " |")
    print("\nwall time (hours):\n")
    print("| scenario | NX lane serial sum | non-NX only, N=3 | non-NX only, N=3 + phone "
          "| ASUS N=2 | ASUS N=3 | ASUS N=4 | ASUS N=3 + phone |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    non_nx = [j for j in jobs if j["payload"]["backend"] != "nx"]
    for scen in SCEN:
        nxs = sum(run_wall(j, scen)[0] for j in jobs if j["payload"]["backend"] == "nx")
        row = [nxs, makespan(non_nx, scen, 3), makespan(non_nx, scen, 3, phone=True)]
        row += [makespan(jobs, scen, n) for n in (2, 3, 4)]
        row.append(makespan(jobs, scen, 3, phone=True))
        print(f"| {scen} | " + " | ".join(f"{x / 3600:.1f}" for x in row) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
