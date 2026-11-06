"""Read the private content repos (cad-skills, strategies, data) at a pinned ref.

A :class:`Source` is a repo folder plus an optional git ref. With a ref, files are read from the
git object store (``git archive`` of that commit), so the run is pinned to ``git_sha`` whatever
the checkout's working tree holds; without one, the working tree is copied and ``git_sha`` is the
HEAD commit (``dirty`` when the tree has local changes). Stdlib only (Termux).
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


class SourceError(RuntimeError):
    pass


def _git(repo: Path, *args: str, binary: bool = False):
    try:
        out = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
            capture_output=True, check=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git missing
        raise SourceError("git not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise SourceError(f"git {' '.join(args)} in {repo}: "
                          f"{exc.stderr.decode(errors='replace').strip()}") from exc
    return out.stdout if binary else out.stdout.decode().strip()


def is_git_repo(path: Path) -> bool:
    try:
        return _git(path, "rev-parse", "--is-inside-work-tree") == "true"
    except SourceError:
        return False


@dataclass
class Source:
    """One content repo at one ref (None = working tree)."""

    root: Path
    ref: str | None = None
    git_sha: str = "unknown"
    dirty: bool = False
    _tar: bytes | None = field(default=None, repr=False)

    @classmethod
    def open(cls, root: str | Path, ref: str | None = None) -> Source:
        root = Path(root).resolve()
        if not root.is_dir():
            raise SourceError(f"{root} is not a directory")
        src = cls(root=root, ref=ref)
        if is_git_repo(root):
            if ref:
                src.git_sha = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
            else:
                src.git_sha = _git(root, "rev-parse", "HEAD")
                src.dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no"))
        elif ref:
            raise SourceError(f"{root} is not a git repo, cannot read ref {ref!r}")
        return src

    @property
    def pin(self) -> str:
        return self.git_sha + ("-dirty" if self.dirty else "")

    # -- reading
    def _archive(self) -> tarfile.TarFile:
        if self._tar is None:
            self._tar = _git(self.root, "archive", "--format=tar", self.git_sha, binary=True)
        return tarfile.open(fileobj=io.BytesIO(self._tar))

    def exists(self, rel: str) -> bool:
        if self.ref is None:
            return (self.root / rel).exists()
        names = self.list(rel)
        return bool(names)

    def read_bytes(self, rel: str) -> bytes:
        rel = _clean(rel)
        if self.ref is None:
            p = self.root / rel
            if not p.is_file():
                raise SourceError(f"{rel} not found in {self.root}")
            return p.read_bytes()
        with self._archive() as tf:
            try:
                m = tf.getmember(rel)
            except KeyError as exc:
                raise SourceError(f"{rel} not found in {self.root}@{self.ref}") from exc
            fh = tf.extractfile(m)
            if fh is None:
                raise SourceError(f"{rel} is not a file in {self.root}@{self.ref}")
            return fh.read()

    def read_text(self, rel: str) -> str:
        return self.read_bytes(rel).decode("utf-8")

    def list(self, prefix: str) -> list[str]:
        """Files (POSIX relative paths) at or below ``prefix``, sorted."""
        prefix = _clean(prefix)
        if self.ref is None:
            base = self.root / prefix
            if base.is_file():
                return [prefix]
            if not base.is_dir():
                return []
            out = []
            for p in sorted(base.rglob("*")):
                rel = p.relative_to(self.root)
                if p.is_file() and not {".git", "__pycache__"} & set(rel.parts):
                    out.append(rel.as_posix())
            return out
        with self._archive() as tf:
            names = [m.name for m in tf.getmembers() if m.isfile()]
        return sorted(n for n in names
                      if n == prefix or n.startswith(prefix.rstrip("/") + "/"))

    def copy_tree(self, prefix: str, dest: Path, *, strip: str | None = None) -> list[Path]:
        """Copy every file below ``prefix`` to ``dest``, keeping the path below ``strip``
        (default: ``prefix``). Returns the written files."""
        strip = _clean(strip if strip is not None else prefix).rstrip("/")
        written = []
        for rel in self.list(prefix):
            sub = rel[len(strip):].lstrip("/") if strip else rel
            if not sub:
                sub = PurePosixPath(rel).name
            target = dest / sub
            self.copy_file(rel, target)
            written.append(target)
        return written

    def copy_file(self, rel: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.ref is None:
            shutil.copyfile(self.root / _clean(rel), target)
        else:
            target.write_bytes(self.read_bytes(rel))
        return target


def _clean(rel: str) -> str:
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        raise SourceError(f"path {rel!r} must be relative and stay inside the repo")
    s = p.as_posix()
    return "" if s == "." else s


def env_dir(var: str, given: str | Path | None) -> Path:
    v = given or os.environ.get(var)
    if not v:
        raise SourceError(f"pass the folder or set {var}")
    return Path(v)


def default_tag_ref(src_root: Path, tag: str) -> str | None:
    """``tag`` if it exists in that repo, else None."""
    try:
        _git(src_root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
        return tag
    except SourceError:
        return None
