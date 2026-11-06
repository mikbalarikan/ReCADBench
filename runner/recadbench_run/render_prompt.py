"""``system_prompt.md`` for models without Claude Code skills (00_DECISIONS D6, LiteLLM loop).

The prompt is CLAUDE.md followed by every skill (each ``SKILL.md`` first, then the skill's other
markdown files in path order), the shared rules and the subagent files, verbatim, each under a
heading that names its run-dir path (so relative references keep working: the files are also in
the run dir and readable with the Read tool). Skill content is identical across model families;
only the packaging differs. Deterministic: same run dir -> byte-identical prompt.
"""

from __future__ import annotations

from pathlib import Path

HEADER = ("You are working inside a ReCADBench run directory. The files below are your "
          "instructions; every path is relative to the run directory. Tools: the file tools "
          "(Read, Write, Edit, Glob, Grep) and the MCP tools listed in your tool schema. A gate "
          "checks every call; a denied call returns the reason as an error.\n")


REFERENCE_DIRS = ("references",)
SIZE_WARN = 300_000  # bytes; above this the prompt dominates a non-Claude model's context


def _md_files(folder: Path) -> list[Path]:
    files = sorted(p for p in folder.rglob("*.md") if p.is_file())
    skill = [p for p in files if p.name == "SKILL.md" and p.parent == folder]
    return skill + [p for p in files if p not in skill]


def _is_reference(f: Path, skill_dir: Path) -> bool:
    return any(part in REFERENCE_DIRS for part in f.relative_to(skill_dir).parts[:-1])


def _title(f: Path) -> str:
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()[:120]
    return ""


def render(run_dir: str | Path) -> str:
    """Skill bodies are inlined; files under a skill's ``references/`` folder are listed (path,
    size, title) and read on demand with the Read tool, the way Claude Code loads them
    (progressive disclosure): the NX skillset's references alone are ~0.7 MB."""
    run = Path(run_dir)
    parts = [HEADER, "\n# CLAUDE.md\n\n", (run / "CLAUDE.md").read_text(encoding="utf-8")]
    skills = run / ".claude" / "skills"
    if skills.is_dir():
        for sk in sorted(p for p in skills.iterdir() if p.is_dir()):
            refs = []
            for f in _md_files(sk):
                if _is_reference(f, sk):
                    refs.append(f)
                    continue
                parts.append(f"\n\n# Skill file `{f.relative_to(run).as_posix()}`\n\n")
                parts.append(f.read_text(encoding="utf-8"))
            if refs:
                parts.append(f"\n\n# Reference files of `{sk.relative_to(run).as_posix()}` "
                             "(read them with the Read tool when the skill points to them)\n\n")
                parts += [f"- `{f.relative_to(run).as_posix()}` ({f.stat().st_size} bytes)"
                          f"{': ' + _title(f) if _title(f) else ''}\n" for f in refs]
    for folder in (run / "_shared", run / ".claude" / "agents"):
        if folder.is_dir():
            for f in sorted(folder.glob("*.md")):
                parts.append(f"\n\n# File `{f.relative_to(run).as_posix()}`\n\n")
                parts.append(f.read_text(encoding="utf-8"))
    text = "".join(parts).replace("\r\n", "\n")
    return text if text.endswith("\n") else text + "\n"


def write_system_prompt(run_dir: str | Path) -> Path:
    out = Path(run_dir) / "system_prompt.md"
    out.write_text(render(run_dir), encoding="utf-8", newline="\n")
    return out


def main_render(a) -> int:
    print(write_system_prompt(a.run_dir))
    return 0
