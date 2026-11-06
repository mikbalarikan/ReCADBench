"""``recadbench compose`` -> a self-contained run dir (00_DECISIONS D6).

    recadbench compose --strategy B --skillset nx@v1 --part 017 [--track E] [--harness-level H3]
    recadbench compose --strategy oracle --skillset none --backend nx --part 017 --track T \
        --seed-plan job/seed_plan.json

Layout written (D6, strategies/README "Compose contract", cad-skills skillset.yaml)::

    runs/<run_id>/
      CLAUDE.md                          task + STRATEGY + SKILLSET + gate rules + conflict rule
      .claude/skills/strategy-<id>/      SKILL.md, playbook.md, stages/, shared/
      .claude/skills/<skill>/            each skillset skill folder
      .claude/agents/                    skillset subagents
      .claude/examples/                  skillset examples (skills link ../../examples/)
      .claude/settings.json              PreToolUse + gate_hook (and permissions)
      .mcp.json                          retk (strategy subset; omitted when empty) + L1 MCP server
      _shared/                           skillset shared docs (none: only ir_execution.md)
      lib/                               skillset helper code (read / import only)
      .recadbench/{gate.json, backend.yaml, compose.json}
      run_manifest.json
      input/ (input.stl, task.yaml)  plan/ (re_plan.v1.json only in Track T)  cad/  out/

Ground truth: compose never reads ``RECADBENCH_GT_DIR``. Track T receives the scanner-frame seed
plan from the job payload (``--seed-plan``, made by ``recadbench seed-plan`` on the evaluator host,
:mod:`recadbench_run.seed`). :func:`compose` ends with :func:`artifacts.gt_leak_check` when a GT
dir is visible on this host and refuses the run dir if any GT file content reached it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .budgets import Budgets
from .gate_hook import BACKEND_FILE, GATE_FILE, STATE_DIR, load_policy
from .sources import Source, SourceError, default_tag_ref

SKILLSET_RE = re.compile(r"^(?P<b>[a-z0-9]+)@(?P<v>v[0-9]+)$")
BACKENDS = ("nx", "build123d", "solidcraft", "freecad")
DEFAULT_HARNESS = "H3"
SECRET_ENV = ("RECADBENCH_GT_DIR", "RECADBENCH_GH_TOKEN", "RECADBENCH_QUEUE_TOKEN")


class ComposeError(RuntimeError):
    pass


@dataclass
class ComposeRequest:
    strategy: str
    skillset: str  # "<b>@vN" | "none"
    part_id: str
    out: Path
    backend: str | None = None
    track: str | None = None
    harness_level: str = DEFAULT_HARNESS
    model: str = "claude-sonnet-5-5"
    provider: str = "anthropic"
    agent_backend: str = "claude_agent_sdk"
    replicate: int = 0
    run_id: str | None = None
    seed_plan: Path | dict | None = None
    budgets: dict = field(default_factory=dict)
    skills_dir: Path | None = None
    strategies_dir: Path | None = None
    data_dir: Path | None = None
    skillset_ref: str | None = None
    strategy_ref: str | None = None
    force: bool = False
    gt_dir_check: bool = True


def backends_dir() -> Path:
    from recad_cli.cli import backends_dir as bd

    return bd()


def load_backend(backend: str) -> tuple[dict, bytes]:
    path = backends_dir() / backend / "backend.yaml"
    if not path.is_file():
        raise ComposeError(f"no backend.yaml for {backend!r} at {path}")
    raw = path.read_bytes()
    return yaml.safe_load(raw.decode("utf-8")), raw


def _validate(doc: Any, schema: str, what: str) -> None:
    from recadbench_spec import validate_document

    rep = validate_document(doc, schema)
    if not rep.ok:
        raise ComposeError(f"{what} is not valid ({schema}): "
                           + "; ".join(f"{e.path} {e.message}" for e in rep.errors[:4]))


def resolve_track(strategy: str, backend: str, track: str | None) -> str:
    if track:
        return track
    if backend == "none":
        return "S"
    return "T" if strategy == "oracle" else "E"


def check_pairing(track: str, strategy: str, skillset: str, backend: str) -> None:
    """D4 rules (also in the run_manifest schema): T = oracle on a backend, S = no backend,
    E = a real strategy on a backend."""
    if track == "T" and (strategy != "oracle" or backend == "none"):
        raise ComposeError("Track T is strategy oracle on a CAD backend")
    if track == "S" and (backend != "none" or skillset != "none"):
        raise ComposeError("Track S is plan-only: --skillset none --backend none")
    if track == "E" and (strategy == "oracle" or backend == "none"):
        raise ComposeError("Track E needs a strategy other than oracle and a CAD backend")
    if strategy == "oracle" and track != "T":
        raise ComposeError("strategy oracle runs only in Track T")


def default_run_id(req: ComposeRequest, track: str, backend: str) -> str:
    sk = req.skillset.replace("@", "-") if req.skillset != "none" else f"none-{backend}"
    raw = f"{track}_{req.strategy}_{sk}_{req.model}_{req.part_id}_r{req.replicate}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def _mcp_command(cmd: str) -> str:
    """Absolute path of an MCP server command: this interpreter's scripts dir first."""
    scripts = Path(sys.executable).parent
    for cand in (scripts / cmd, scripts / f"{cmd}.exe"):
        if cand.is_file():
            return str(cand)
    return shutil.which(cmd) or cmd


def agent_env(run_dir: Path, extra: dict | None = None) -> dict[str, str]:
    """Environment for the agent process and its MCP servers: no GT dir, no tokens; the runner's
    scripts dir first on PATH (``recadbench nx-run``)."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    scripts = str(Path(sys.executable).parent)
    env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    env["RECADBENCH_RUN_DIR"] = str(run_dir)
    if extra:
        env.update(extra)
    return env


# ------------------------------------------------------------------------------ CLAUDE.md
def render_claude_md(ctx: dict) -> str:
    t = ctx["task"]
    lines = [f"# ReCADBench run {ctx['run_id']}", "",
             f"Part `{t['part_id']}`, track **{ctx['track']}**, strategy **{ctx['strategy_id']}**, "
             f"skillset **{ctx['skillset']}**, backend **{ctx['backend']}**, harness level "
             f"{ctx['harness_level']}.", "", "## Task", "", t.get("instructions", "").strip(), "",
             "Inputs: `input/input.stl` (the scan, scanner frame, mm) and `input/task.yaml` "
             "(tolerance class, budgets, deliverables). Work only inside this folder. Paths "
             "that skills and agents name (`_shared/...`, `lib/...`, `plan/...`, `cad/...`) are "
             "relative to this folder (the run dir), not to the skill's own folder.", "",
             "Deliverables: " + ", ".join(f"`{d}`" for d in t.get("deliverables") or []) + "."]
    if ctx["track"] == "T":
        lines += ["", "`plan/re_plan.v1.json` is given by the harness (the reference plan, mapped "
                  "into the scanner frame). It is read-only; execute it."]
    if ctx["track"] == "S":
        lines += ["", "This is a **plan-only** run: there is no CAD program. Deliver "
                  "`plan/re_plan.vN.json` only."]
    lines += ["", "## STRATEGY (what to build, reasoning order)", "",
              f"Use the skill `strategy-{ctx['strategy_id']}` "
              f"(`.claude/skills/strategy-{ctx['strategy_id']}/SKILL.md`) for the whole run; "
              "read its playbook first."]
    if ctx["retk_tools"]:
        lines += ["", "Scan analysis: the `retk` MCP tools only (" +
                  ", ".join(f"`{x}`" for x in ctx["retk_tools"]) + ")."]
    else:
        lines += ["", "No scan-analysis tools in this run."]
    lines += ["", "## SKILLSET (how to operate the CAD program)", ""]
    if ctx["backend"] == "none":
        lines += ["None (plan-only)."]
    else:
        if ctx["skills"]:
            lines += ["Skills: " + ", ".join(f"`{s}`" for s in ctx["skills"]) + "."]
        if ctx["agents"]:
            lines += ["Subagents: " + ", ".join(f"`{a}`" for a in ctx["agents"]) + "."]
        if ctx["shared"]:
            lines += ["Shared rules: " + ", ".join(f"`{s}`" for s in ctx["shared"]) + "."]
        if ctx["lib"]:
            lines += ["Helper code (read / import only): " +
                      ", ".join(f"`{x}`" for x in ctx["lib"]) + "."]
        lines += [f"Native deliverable: `{ctx['native_file']}`. After the session the benchmark "
                  "exports and counts that file in a clean process; `out/output.step` is "
                  "regenerated from it."]
        if ctx["kind"] == "mcp":
            lines += [f"CAD program: the `{ctx['backend']}` MCP server (tools "
                      f"`{', '.join(ctx['cad_prefixes'])}*`)."]
        else:
            lines += ["CAD program: shell commands " +
                      ", ".join(f"`{c}`" for c in ctx["allowed_commands"]) +
                      " (optional `--timeout S`), nothing else."]
    lines += ["", "## Gate (enforced on every tool call)", "",
              "- No shell" + ("" if ctx["kind"] != "cli" else
                              " except the CAD command above") + "; no web access.",
              "- Any CAD call needs a schema-valid `plan/re_plan.vN.json` first.",
              "- After the CAD source changed (a file under `cad/`), the next execution needs "
              "plan revision N+1 (`revision`: N+1 and a `change_log` line).",
              "- CAD tools that import or analyse the scan are denied; the scan is analysed with "
              "retk only. Never import or convert the scan into the CAD model.",
              "- Executed or harness-given plan revisions, `input/`, `lib/`, `.claude/` and the "
              "manifests are read-only.", "",
              "## Conflict rule", "",
              "The strategy decides **what** is built and the reasoning order; the skillset "
              "decides **how** the CAD program is operated. Skillsets contain no reverse-"
              "engineering method; strategies name no CAD command. If they disagree, follow the "
              "strategy for what and the skillset for how.", ""]
    return "\n".join(lines)


# -------------------------------------------------------------------------------- compose
def compose(req: ComposeRequest) -> Path:
    from .sources import env_dir

    policy = load_policy()
    out = Path(req.out).resolve()
    if out.exists() and any(out.iterdir()):
        if not req.force:
            raise ComposeError(f"{out} exists and is not empty (use --force)")
        shutil.rmtree(out)
    # -- skillset / backend
    if req.skillset == "none":
        backend = req.backend or "none"
        version = None
    else:
        m = SKILLSET_RE.match(req.skillset)
        if not m:
            raise ComposeError(f"--skillset must be '<backend>@vN' or 'none', got {req.skillset}")
        backend, version = m.group("b"), m.group("v")
        if req.backend and req.backend != backend:
            raise ComposeError(f"--backend {req.backend} contradicts skillset {req.skillset}")
    if backend != "none" and backend not in BACKENDS:
        raise ComposeError(f"unknown backend {backend!r}")
    track = resolve_track(req.strategy, backend, req.track)
    check_pairing(track, req.strategy, req.skillset, backend)

    data_dir = env_dir("RECADBENCH_DATA_DIR", req.data_dir)
    strat_src = Source.open(env_dir("RECADBENCH_STRATEGIES_DIR", req.strategies_dir),
                            req.strategy_ref)
    skills_root = None
    if backend != "none" or req.skillset != "none":
        skills_root = env_dir("RECADBENCH_SKILLS_DIR", req.skills_dir)
    skills_src = None
    if req.skillset != "none":
        ref = req.skillset_ref or default_tag_ref(Path(skills_root), f"{backend}-{version}")
        if ref is None:
            raise ComposeError(f"tag {backend}-{version} not found in {skills_root}; pass "
                               "--skillset-ref <git ref> (recorded in the manifest)")
        skills_src = Source.open(skills_root, ref)
    elif skills_root is not None:
        skills_src = Source.open(skills_root, req.skillset_ref)

    # -- manifests
    sdir = f"strategies/{req.strategy}"
    strategy = yaml.safe_load(strat_src.read_text(f"{sdir}/strategy.yaml"))
    _validate(strategy, "strategy_manifest", f"{sdir}/strategy.yaml")
    if strategy["id"] != req.strategy:
        raise ComposeError(f"{sdir}/strategy.yaml has id {strategy['id']!r}")
    if bool(strategy.get("inject_gt_plan")) != (track == "T"):
        raise ComposeError("inject_gt_plan must be true exactly for the Track T oracle")
    skillset = None
    if req.skillset != "none":
        skillset = yaml.safe_load(skills_src.read_text(f"{backend}/skillset.yaml"))
        _validate(skillset, "skillset_manifest", f"{backend}/skillset.yaml")
        if skillset["version"] != version or skillset["backend"]["id"] != backend:
            raise ComposeError(f"{backend}/skillset.yaml at {skills_src.ref} is "
                               f"{skillset['backend']['id']}@{skillset['version']}, not "
                               f"{req.skillset}")
    bman, braw = ({}, b"") if backend == "none" else load_backend(backend)
    if bman:
        _validate(bman, "backend_manifest", f"backends/{backend}/backend.yaml")
    bpol = (policy.get("backends") or {}).get(backend, {})

    part_dir = data_dir / "parts" / req.part_id
    task_p = part_dir / "task.yaml"
    if not task_p.is_file():
        raise ComposeError(f"no task.yaml for {req.part_id} in {data_dir}")
    task = yaml.safe_load(task_p.read_text(encoding="utf-8"))
    _validate(task, "task", f"{req.part_id}/task.yaml")
    split = _split_of(data_dir, req.part_id)
    if track == "T" and split != "dev":
        raise ComposeError(f"Track T exposes the reference plan: dev parts only "
                           f"({req.part_id} is {split or 'in no split'})")

    seed = None
    if track == "T":
        if req.seed_plan is None:
            raise ComposeError("Track T needs --seed-plan (the scanner-frame reference plan from "
                               "the job payload; `recadbench seed-plan` on the evaluator host)")
        seed = req.seed_plan if isinstance(req.seed_plan, dict) else json.loads(
            Path(req.seed_plan).read_text(encoding="utf-8"))
        _validate(seed, "re_plan_ir", "seed plan")
        if seed.get("part_id") != req.part_id or seed.get("revision") != 1:
            raise ComposeError("seed plan must be revision 1 of this part")
    elif req.seed_plan is not None:
        raise ComposeError(f"--seed-plan is only for Track T (this is Track {track})")

    # -- write
    out.mkdir(parents=True, exist_ok=True)
    for d in ("input", "plan", "cad", "out", ".claude/skills", STATE_DIR):
        (out / d).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(part_dir / "input.stl", out / "input" / "input.stl")
    shutil.copyfile(task_p, out / "input" / "task.yaml")

    sk_dest = out / ".claude" / "skills" / f"strategy-{req.strategy}"
    strat_src.copy_file(f"{sdir}/{strategy['skill']}", sk_dest / "SKILL.md")
    strat_src.copy_file(f"{sdir}/{strategy['playbook']}", sk_dest / "playbook.md")
    for st in strategy["stages"]:
        strat_src.copy_file(f"{sdir}/stages/{st}.md", sk_dest / "stages" / f"{st}.md")
    for rel in strat_src.list("shared"):
        if rel.endswith(".md"):
            strat_src.copy_file(rel, sk_dest / "shared" / Path(rel).name)

    skills, agents, shared, libs, warnings = [], [], [], [], []
    if skillset is not None:
        base = backend
        for s in skillset["skills"]:
            name = Path(s).name
            if not skills_src.copy_tree(f"{base}/{s}", out / ".claude" / "skills" / name):
                raise ComposeError(f"skill {base}/{s} listed in skillset.yaml is empty or "
                                   f"missing at {skills_src.ref}")
            skills.append(name)
        if skills_src.list(f"{base}/examples"):
            skills_src.copy_tree(f"{base}/examples", out / ".claude" / "examples")
        for a in skillset.get("agents") or []:
            skills_src.copy_file(f"{base}/{a}", out / ".claude" / "agents" / Path(a).name)
            agents.append(Path(a).stem)
        for lib in skillset.get("lib") or []:
            skills_src.copy_tree(f"{base}/{lib}", out / "lib" / Path(lib).name)
            libs.append(f"lib/{Path(lib).name}")
        for sh in skillset.get("shared") or ["_shared/ir_execution.md",
                                             "_shared/single_solid_rules.md"]:
            if not skills_src.list(sh):
                # e.g. nx-v1 was tagged before _shared/ existed (H4 < H6): the pin is kept,
                # the gap is recorded in the manifest (STATUS open question)
                warnings.append(f"{sh} listed in skillset.yaml is missing at {skills_src.ref}")
                continue
            skills_src.copy_file(sh, out / sh)
            shared.append(sh)
    elif backend != "none" and skills_src is not None:
        # bare-L1 baseline: only what is needed to read the IR (02_REPO_LAYOUTS)
        skills_src.copy_file("_shared/ir_execution.md", out / "_shared" / "ir_execution.md")
        shared.append("_shared/ir_execution.md")

    allowed_retk = set(policy["harness_levels"][req.harness_level])
    retk_tools = [t for t in strategy["toolkit_tools"] if t in allowed_retk]
    mcp: dict[str, dict] = {}
    if retk_tools:
        mcp["retk"] = {"type": "stdio", "command": _mcp_command("retk-server"),
                       "args": ["--tools", ",".join(retk_tools)]}
    iface = bman.get("interface") or {}
    if iface.get("kind") == "mcp":
        cfg = iface["mcp"]
        prefix = iface["cad_prefixes"]["all"][0]
        server = prefix[len("mcp__"):].rstrip("_")
        env = {}
        if libs:
            env["PYTHONPATH"] = str(out / "lib")
        mcp[server] = {"type": "stdio", "command": _mcp_command(cfg["command"]),
                       "args": list(cfg.get("args") or []), **({"env": env} if env else {})}
    (out / ".mcp.json").write_text(json.dumps({"mcpServers": mcp}, indent=2) + "\n",
                                   encoding="utf-8", newline="\n")

    hook_cmd = f'"{sys.executable}" -m recadbench_run.gate_hook --run-dir "{out}"'
    settings = {
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command",
                                                              "command": hook_cmd}]}]},
        "permissions": {"deny": ["WebFetch", "WebSearch"]},
        "enableAllProjectMcpServers": True,
    }
    (out / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n",
                                                   encoding="utf-8", newline="\n")
    if braw:
        (out / BACKEND_FILE).write_bytes(braw)
    seeded = []
    if seed is not None:
        (out / "plan" / "re_plan.v1.json").write_text(json.dumps(seed, indent=2) + "\n",
                                                      encoding="utf-8", newline="\n")
        seeded = [1]
    gate = {"part_id": req.part_id, "track": track, "backend": backend,
            "retk_tools": retk_tools, "seeded_revisions": seeded,
            "native_file": bpol.get("native_file")}
    (out / GATE_FILE).write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8",
                                 newline="\n")

    run_id = req.run_id or default_run_id(req, track, backend)
    budgets = Budgets.merge(task.get("budgets") or {}, req.budgets)
    manifest = {
        "run_id": run_id, "track": track, "part_id": req.part_id, "split": split or "dev",
        "replicate": int(req.replicate),
        "strategy": {"id": req.strategy, "git_sha": strat_src.pin,
                     "version": str(strategy.get("version"))},
        "skillset": {"id": req.skillset, "git_sha": skills_src.pin if (
            skills_src is not None and req.skillset != "none") else "none"},
        "backend": {"id": backend,
                    **({"software_version": f"{bman['software']['name']} "
                                            f"{bman['software']['version']}"} if bman else {})},
        "model": {"id": req.model, "provider": req.provider},
        "spec_version": _spec_version(),
        "budgets": budgets.to_manifest(),
        "harness_level": req.harness_level,
        "agent_driver": req.agent_backend,
        "compose": {"runner_version": __version__, "strategy_ref": strat_src.ref,
                    "skillset_ref": skills_src.ref if skills_src else None,
                    "skills_sha": skills_src.pin if skills_src else None,
                    "data_sha": Source.open(data_dir).pin if (data_dir / ".git").exists()
                    else "unknown",
                    "backend_yaml_sha256": _sha(braw) if braw else None,
                    "retk_tools": retk_tools, "seed_plan": bool(seed),
                    "warnings": warnings},
    }
    if req.agent_backend in ("claude_agent_sdk", "litellm_loop"):
        manifest["agent_backend"] = req.agent_backend
    if backend != "none" and bman:
        manifest["backend"]["backend_sha"] = _sha(braw)
    _validate(manifest, "run_manifest", "run_manifest.json")
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                           encoding="utf-8", newline="\n")

    ctx = {"run_id": run_id, "task": task, "track": track, "strategy_id": req.strategy,
           "skillset": req.skillset, "backend": backend, "harness_level": req.harness_level,
           "retk_tools": retk_tools, "skills": skills, "agents": agents, "shared": shared,
           "lib": libs, "native_file": bpol.get("native_file"), "kind": iface.get("kind"),
           "cad_prefixes": (iface.get("cad_prefixes") or {}).get("all") or [],
           "allowed_commands": iface.get("allowed_commands") or []}
    (out / "CLAUDE.md").write_text(render_claude_md(ctx), encoding="utf-8", newline="\n")
    from .render_prompt import write_system_prompt

    write_system_prompt(out)
    (out / STATE_DIR / "compose.json").write_text(json.dumps(ctx, indent=2, default=str) + "\n",
                                                  encoding="utf-8", newline="\n")

    from .artifacts import gt_leak_check

    if req.gt_dir_check and os.environ.get("RECADBENCH_GT_DIR"):
        leaks = gt_leak_check(out, Path(os.environ["RECADBENCH_GT_DIR"]), req.part_id,
                              allow_seed=track == "T")
        if leaks:
            shutil.rmtree(out, ignore_errors=True)
            raise ComposeError("ground truth reached the run dir: " + "; ".join(leaks))
    return out


def _sha(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def _spec_version() -> str:
    from recadbench_spec import SPEC_VERSION

    return SPEC_VERSION


def _split_of(data_dir: Path, part_id: str) -> str | None:
    for split in ("dev", "test"):
        p = data_dir / "splits" / f"{split}.txt"
        if p.is_file() and part_id in p.read_text(encoding="utf-8").split():
            return split
    return None


def add_arguments(ap) -> None:
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--skillset", required=True, help="<backend>@vN or none")
    ap.add_argument("--backend", help="with --skillset none: the bare L1 backend (or none)")
    ap.add_argument("--part", required=True)
    ap.add_argument("--track", choices=["T", "S", "E"])
    ap.add_argument("--harness-level", default=DEFAULT_HARNESS, choices=["H0", "H1", "H2", "H3"])
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--agent-backend", default="claude_agent_sdk",
                    choices=["claude_agent_sdk", "litellm_loop", "replay"])
    ap.add_argument("--replicate", type=int, default=0)
    ap.add_argument("--run-id")
    ap.add_argument("--seed-plan", type=Path, help="Track T: scanner-frame seed plan (job payload)")
    ap.add_argument("--budget", action="append", default=[], metavar="KEY=VALUE",
                    help="override a budget (max_wall_s, max_turns, max_tokens, max_cost_usd)")
    ap.add_argument("--skills-dir", type=Path, help="recadbench-cad-skills (RECADBENCH_SKILLS_DIR)")
    ap.add_argument("--strategies-dir", type=Path,
                    help="recadbench-strategies (RECADBENCH_STRATEGIES_DIR)")
    ap.add_argument("--data-dir", type=Path, help="recadbench-data (RECADBENCH_DATA_DIR)")
    ap.add_argument("--skillset-ref", help="git ref of the skills repo (default: tag <b>-vN)")
    ap.add_argument("--strategy-ref", help="git ref of the strategies repo (default: working tree)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--force", action="store_true")


def request_from_args(a) -> ComposeRequest:
    budgets = {}
    for kv in a.budget:
        k, _, v = kv.partition("=")
        budgets[k] = float(v) if "." in v else int(v)
    return ComposeRequest(
        strategy=a.strategy, skillset=a.skillset, part_id=a.part, out=a.out, backend=a.backend,
        track=a.track, harness_level=a.harness_level, model=a.model, provider=a.provider,
        agent_backend=a.agent_backend, replicate=a.replicate, run_id=a.run_id,
        seed_plan=a.seed_plan, budgets=budgets, skills_dir=a.skills_dir,
        strategies_dir=a.strategies_dir, data_dir=a.data_dir, skillset_ref=a.skillset_ref,
        strategy_ref=a.strategy_ref, force=a.force)


def main_compose(a) -> int:
    try:
        out = compose(request_from_args(a))
    except (ComposeError, SourceError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, "run_dir": str(out)}))
    return 0
