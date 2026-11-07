"""The H9 pilot experiment expands to the hand-checked run count (PILOT_ESTIMATE.md section 1)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from recadbench_orch.expand import expand, load, summary

PILOT = Path(__file__).resolve().parents[1] / "experiments" / "pilot.yaml"


def test_pilot_run_count():
    exp = load(PILOT)
    jobs = expand(exp)
    # 3 parts x 2 models x 1 replicate = 6 per (track, strategy, skillset) cell
    assert summary(jobs) == {
        "runs": 60,
        "by_track": {"E": 24, "S": 6, "T": 30},
        "by_backend": {"build123d": 24, "none": 6, "nx": 30},
        "by_model": {"claude-opus-5-5": 30, "claude-sonnet-5-5": 30},
        "by_strategy": {"B": 18, "D": 12, "oracle": 30},
    }
    cells = Counter((j["payload"]["track"], j["payload"]["strategy"], j["payload"]["skillset"],
                     j["payload"]["backend"]) for j in jobs)
    assert set(cells.values()) == {6} and len(cells) == 10
    assert sum(1 for j in jobs if j["requires"] == ["nx"]) == 30


def test_pilot_payload_pins():
    jobs = expand(load(PILOT))
    p = {j["payload"]["skillset"]: j["payload"] for j in jobs}
    assert {j["payload"]["harness_level"] for j in jobs} == {"H3"}
    assert {j["payload"]["strategy_ref"] for j in jobs} == {
        "6cc9798aeec278a8e8875752e21bcc340d95e95e"}
    assert p["build123d@v1"]["skillset_ref"] == "build123d-v1"
    assert p["nx@v1"]["skillset_ref"] == "nx-v1"
    assert p["nx@v2"]["skillset_ref"] == "nx-v2"
    assert {j["payload"]["skillset"] for j in jobs if j["payload"]["track"] == "E"} == {
        "build123d@v1", "nx@v2"}
    assert p["none"]["skillset_ref"] == "846414c106a885b10e486bba2e72c6e457502121"
    assert {j["payload"]["budgets"]["max_cost_usd"] for j in jobs} == {60}
    assert {j["payload"]["agent_backend"] for j in jobs} == {"claude_agent_sdk"}
