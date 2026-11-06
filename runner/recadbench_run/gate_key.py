"""Per-run gate secret: the HMAC key that authenticates the gate state and the compose manifest.

Why: the gate keeps its state (which plan revision executed which CAD source) in
``.recadbench/gate_state.json``. Agent code runs as the same OS user as the gate, so any check
value the agent can recompute (a plain sha256, a digest file whose name is derivable from the
run dir) can be forged. An HMAC under a secret the agent's processes never see cannot.

Where the key lives (never in the run dir):

* the **key store**, one JSON entry per run ``<key dir>/<random 32 hex>.json``
  ``{"run", "key", "state_mac", "manifest_mac", "created"}``. The entry name is random and is
  published nowhere; the gate finds the entry by its ``run`` field.
* ``<key dir>`` = ``RECADBENCH_GATE_KEY_DIR`` (set it to a random, runner-private folder),
  default ``~/.recadbench-private/gate-keys``. The variable reaches the gate (runner process,
  Claude Code hook) but is removed from every process that runs agent code: the CAD MCP servers
  (``.mcp.json`` sets it to ""; the dispatcher drops it), ``recadbench nx-run``'s NX process
  (``nx_run.STRIP_ENV``) and finish's export / extract (``finish.clean_env``).

Residual risk (README "Security model"): code that escapes the CAD code policy runs as the same
OS user and can list the default key dir (or any dir it can guess) and read the key. Only OS-level
isolation (agent processes under a user without read access to the key dir and the GT store)
closes that.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

KEY_DIR_ENV = "RECADBENCH_GATE_KEY_DIR"
# variables that must never reach a process running agent code
GATE_ENV = (KEY_DIR_ENV,)


def key_dir() -> Path:
    v = os.environ.get(KEY_DIR_ENV)
    return Path(v) if v else Path.home() / ".recadbench-private" / "gate-keys"


def _norm(run_dir: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(Path(run_dir).resolve())))


def _entries(run: str):
    d = key_dir()
    if not d.is_dir():
        return
    for p in sorted(d.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("run") == run:
            yield p, doc


def _write(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, p)


class RunKey:
    """The key-store entry of one run dir."""

    def __init__(self, path: Path, doc: dict):
        self.path = path
        self.doc = doc
        self.key = bytes.fromhex(doc["key"])

    @classmethod
    def create(cls, run_dir: str | Path) -> RunKey:
        """A fresh key for ``run_dir`` (compose; a forced re-compose starts over)."""
        run = _norm(run_dir)
        for p, _ in list(_entries(run)):
            with contextlib.suppress(OSError):
                p.unlink()
        doc = {"run": run, "key": secrets.token_hex(32), "state_mac": None,
               "manifest_mac": None, "created": round(time.time(), 3)}
        p = key_dir() / f"{secrets.token_hex(16)}.json"
        _write(p, doc)
        return cls(p, doc)

    @classmethod
    def load(cls, run_dir: str | Path) -> RunKey | None:
        found = list(_entries(_norm(run_dir)))
        if len(found) != 1:
            return None
        p, doc = found[0]
        try:
            return cls(p, doc)
        except (KeyError, ValueError, TypeError):
            return None

    def mac(self, label: str, data: bytes) -> str:
        return hmac.new(self.key, label.encode("ascii") + b"\0" + self.doc["run"].encode("utf-8")
                        + b"\0" + data, hashlib.sha256).hexdigest()

    def verify(self, label: str, data: bytes, want: str | None) -> bool:
        return bool(want) and hmac.compare_digest(self.mac(label, data), str(want))

    @contextlib.contextmanager
    def locked(self, timeout: float = 15.0, stale: float = 60.0):
        """Exclusive lock (a lock file next to the entry) around read-verify and write-MAC of
        the gate state: the in-process SDK hook and the project's command hook may run at the
        same time, and a state written between another process's read and MAC update must not
        look tampered."""
        lock = self.path.with_name(self.path.name + ".lock")
        t0 = time.time()
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                with contextlib.suppress(OSError):
                    if time.time() - lock.stat().st_mtime > stale:
                        lock.unlink()  # a crashed holder
                        continue
                if time.time() - t0 > timeout:
                    raise TimeoutError(f"gate key lock {lock} held for {timeout:g} s") from None
                time.sleep(0.02)
        try:
            os.close(fd)
            self.refresh()
            yield self
        finally:
            with contextlib.suppress(OSError):
                lock.unlink()

    def set(self, **fields) -> None:
        self.doc.update(fields)
        _write(self.path, self.doc)

    def refresh(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            self.doc = json.loads(self.path.read_text(encoding="utf-8"))

    def delete(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()


def scrub(env: dict[str, str]) -> dict[str, str]:
    """``env`` without the gate variables (for processes that run agent code)."""
    return {k: v for k, v in env.items() if k not in GATE_ENV}
