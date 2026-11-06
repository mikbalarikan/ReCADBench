"""Synthetic helpers for the runner tests (no file from ReCADBench-gt / -data / private repos).

* ``mini_run(tmp_path, backend)`` - a run dir with the gate config and the REAL backend.yaml of
  this repo (the gate must read denied_tools from it), for gate unit tests;
* ``content_repos(tmp_path)`` - git repos standing in for recadbench-strategies (oracle, B),
  recadbench-cad-skills (build123d@v1, tag build123d-v1) and recadbench-data (one synthetic
  part with a generated scan);
* ``synthetic_gt(root)`` - a GT store for that part (the evaluator's synthetic "scan" part:
  known ``T_scan_to_gt``), built with recadbench-eval; host-independent.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
BACKENDS = REPO / "backends"
POLICY = REPO / "runner" / "recadbench_run" / "gate_policy.yaml"


def plan_doc(part_id: str = "syn_part", revision: int = 1, change_log=None) -> dict:
    d = {
        "spec_version": "0.3.0", "part_id": part_id, "revision": revision, "units": "mm",
        "datums": [{"id": "D1", "role": "primary", "kind": "plane", "origin": [0, 0, 0],
                    "direction": [0, 0, 1], "x_dir": [1, 0, 0]}],
        "parameters": [{"name": "w", "value": 20.0}],
        "features": [{"id": "F1", "type": "extrude", "operation": "new",
                      "sketch": {"plane": "D1", "entities": [
                          {"kind": "rect", "center": [0, 0], "width": {"ref": "w"},
                           "height": 10}]},
                      "params": {"distance": 5, "direction": [0, 0, 1]}}],
    }
    if revision > 1:
        d["change_log"] = change_log or [f"v{revision}: re-execute after a CAD error"]
    return d


def write_plan(run: Path, revision: int = 1, part_id: str = "syn_part", **kw) -> Path:
    p = run / "plan" / f"re_plan.v{revision}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan_doc(part_id, revision, **kw)), encoding="utf-8")
    return p


def mini_run(tmp_path: Path, backend: str = "build123d", retk=("mesh_stats",), seeded=(),
             part_id: str = "syn_part") -> Path:
    run = tmp_path / f"run_{backend}"
    for d in ("input", "plan", "cad", "out", "lib", ".claude", ".recadbench"):
        (run / d).mkdir(parents=True, exist_ok=True)
    (run / "input" / "input.stl").write_text("solid s\nendsolid s\n", encoding="utf-8")
    if backend != "none":
        shutil.copyfile(BACKENDS / backend / "backend.yaml", run / ".recadbench" / "backend.yaml")
    pol = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    native = (pol["backends"].get(backend) or {}).get("native_file")
    (run / ".recadbench" / "gate.json").write_text(json.dumps({
        "part_id": part_id, "track": "T" if seeded else "E", "backend": backend,
        "retk_tools": list(retk), "seeded_revisions": list(seeded), "native_file": native}),
        encoding="utf-8")
    return run


# ------------------------------------------------------------------------- content repos
def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                           "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def _init(root: Path, files: dict[str, str], tag: str | None = None) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q")
    _write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    if tag:
        git(root, "tag", tag)
    return root


STRATEGY_FILES = {
    "strategies/oracle/strategy.yaml": yaml.safe_dump({
        "id": "oracle", "name": "Oracle", "version": "1", "playbook": "playbook.md",
        "stages": ["intake", "execute"], "toolkit_tools": [], "max_refine_iters": 0,
        "inject_gt_plan": True, "skill": "skills/SKILL.md"}),
    "strategies/oracle/playbook.md": "# oracle playbook\n",
    "strategies/oracle/skills/SKILL.md": "---\nname: strategy-oracle\ndescription: x\n---\n# o\n",
    "strategies/oracle/stages/intake.md": "# intake\n",
    "strategies/oracle/stages/execute.md": "# execute\n",
    "strategies/B/strategy.yaml": yaml.safe_dump({
        "id": "B", "name": "Region segmentation", "version": "1", "playbook": "playbook.md",
        "stages": ["plan"], "toolkit_tools": ["mesh_stats", "slice", "fit_primitives",
                                              "self_deviation", "render"],
        "inject_gt_plan": False, "skill": "skills/SKILL.md"}),
    "strategies/B/playbook.md": "# B playbook\n",
    "strategies/B/skills/SKILL.md": "---\nname: strategy-B\ndescription: x\n---\n# B\n",
    "strategies/B/stages/plan.md": "# plan\n",
    "shared/ir_authoring.md": "# IR authoring\n",
    "shared/design_intent_rules.md": "# rules\n",
}

SKILLS_FILES = {
    "build123d/skillset.yaml": yaml.safe_dump({
        "id": "build123d", "version": "v1", "backend": {"id": "build123d"},
        "skills": ["skills/build123d-modeling"], "agents": ["agents/build123d-modeler.md"],
        "lib": ["lib/b3dhelpers"],
        "shared": ["_shared/ir_execution.md", "_shared/single_solid_rules.md"]}),
    "build123d/skills/build123d-modeling/SKILL.md":
        "---\nname: build123d-modeling\ndescription: x\n---\n# modelling\n",
    "build123d/skills/build123d-modeling/references/r.md": "# recipes\n",
    "build123d/agents/build123d-modeler.md": "---\nname: build123d-modeler\n---\n# modeler\n",
    "build123d/lib/b3dhelpers/__init__.py": "from build123d import Plane\n",
    "build123d/examples/e01/part.py": "from build123d import Box\npart = Box(1, 1, 1)\n",
    "_shared/ir_execution.md": "# IR execution\n",
    "_shared/single_solid_rules.md": "# one solid\n",
}


def task_doc(part_id: str) -> dict:
    return {"spec_version": "0.3.0", "part_id": part_id, "input_stl": "input/input.stl",
            "input_kind": "synthetic_degraded", "units": "mm", "tolerance_class": "B",
            "tier": 1, "budgets": {"max_wall_s": 900, "max_turns": 40},
            "deliverables": ["out/output.step", "cad/native", "plan/re_plan.vN.json"],
            "instructions": "Reverse-engineer input/input.stl into one parametric solid."}


def write_scan_stl(path: Path, shape=None, T_gt_to_scan=None) -> None:
    """A tessellation of ``shape`` (default: a 20 x 10 x 5 box) moved into the scanner frame."""
    import numpy as np
    import trimesh

    if shape is None:
        mesh = trimesh.creation.box(extents=(20, 10, 5))
    else:
        from recadbench_eval import brep

        tess = brep.tessellate(shape, 0.05)
        mesh = trimesh.Trimesh(vertices=np.asarray(tess.vertices),
                               faces=np.asarray(tess.triangles))
    if T_gt_to_scan is not None:
        mesh.apply_transform(np.asarray(T_gt_to_scan, dtype=float))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def content_repos(tmp_path: Path, part_id: str = "syn_part", scan_writer=None) -> dict:
    strat = _init(tmp_path / "strategies", STRATEGY_FILES)
    skills = _init(tmp_path / "cad-skills", SKILLS_FILES, tag="build123d-v1")
    data = tmp_path / "data"
    files = {f"parts/{part_id}/task.yaml": yaml.safe_dump(task_doc(part_id)),
             "splits/dev.txt": part_id + "\n", "splits/test.txt": ""}
    _init(data, files)
    (scan_writer or write_scan_stl)(data / "parts" / part_id / "input.stl")
    return {"strategies_dir": strat, "skills_dir": skills, "data_dir": data}


# ------------------------------------------------------------------------- synthetic GT
def evaluator_conftest():
    """The evaluator's synthetic GT builder (evaluator/tests/conftest.py), loaded by path."""
    path = REPO / "evaluator" / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("recadbench_eval_test_conftest", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


