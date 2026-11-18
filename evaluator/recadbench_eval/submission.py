"""ReCADBench Open submission package: validate a ``submission.zip`` without extracting it, then
extract one task at a time into a run folder (docs/benchmark/submission.md, ADR 0006).

Standard library only (the Space and ``tools/sanity_check_submission.py`` import this module
without numpy / OCP).

    sub = inspect_zip("submission.zip", task_ids)        # raises SubmissionError if unusable
    for tid in sub.tasks:
        step = extract_task("submission.zip", sub, tid, f"runs/{tid}")

Layout::

    meta.json                          required (META_REQUIRED keys, see validate_meta)
    <task_id>/output.step              required per solved task (else warning + missing)
    <task_id>/features.json            optional
    <task_id>/plan/re_plan.v<N>.json   optional
    <task_id>/native/**                optional (Verified layer; never extracted here)
    <task_id>/run.json                 optional

``SubmissionError`` messages are user-facing: ASCII only, and they name entries inside the zip
only (never a host path).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

LIMITS = dict(
    zip_bytes=300 * 2**20,
    step_bytes=50 * 2**20,
    total_uncompressed=1200 * 2**20,
    max_entries=2000,
)
META_REQUIRED = (
    "submitter_name",
    "submission_name",
    "agent_url",
    "model",
    "cad_backend",
    "harness",
    "human_in_loop",
    "notes",
    "agree_to_publish",
)

MAX_RATIO = 100  # compression-bomb ratio (uncompressed / compressed)
RATIO_MIN_BYTES = 2**20  # small entries (a 2 kB JSON of spaces) may compress > 100x legitimately
META_MAX_BYTES = 64 * 2**10
NAME_MAX = 80
NOTES_MAX = 500
TEXT_MAX = 200  # model / cad_backend / harness / agent_url

STEP_NAME = "output.step"
_PLAN_RE = re.compile(r"^plan/re_plan\.v[0-9]{1,6}\.json$")
_URL_RE = re.compile(r"^https?://[^\s/?#]+[^\s]*$", re.IGNORECASE)
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CHUNK = 2**20
_COMPRESSIONS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA)
_READ_ERRORS = (zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error)
_OPEN_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError,
                OSError, ValueError, struct.error)
# End-of-central-directory records (APPNOTE 4.3.14-4.3.16), as zipfile parses them
_EOCD_SIG, _EOCD_FMT = b"PK\x05\x06", "<4s4H2LH"
_EOCD_SIZE = struct.calcsize(_EOCD_FMT)  # 22
_LOC64_SIG, _LOC64_FMT = b"PK\x06\x07", "<4sLQL"
_LOC64_SIZE = struct.calcsize(_LOC64_FMT)  # 20
_EOCD64_SIG, _EOCD64_FMT = b"PK\x06\x06", "<4sQ2H2L4Q"
_EOCD64_SIZE = struct.calcsize(_EOCD64_FMT)  # 56
CD_BYTES_PER_ENTRY = 46 + 1024  # fixed header + generous name/extra/comment allowance


class SubmissionError(ValueError):
    """The zip cannot be scored. The message is user-facing (ASCII, zip-internal names only)."""


@dataclass
class Submission:
    meta: dict
    tasks: dict[str, dict[str, str]]  # task_id -> {"output.step": arcname, "features.json": ...}
    sha256: str
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------ helpers
def _q(name: str, limit: int = 120) -> str:
    """An entry name safe to put in a user-facing message (ASCII, bounded, quoted)."""
    s = name.encode("ascii", "backslashreplace").decode("ascii")
    s = _CTRL_RE.sub("?", s)
    if len(s) > limit:
        s = s[: limit - 3] + "..."
    return f"'{s}'"


def _mib(n: int) -> str:
    return f"{n / 2**20:.0f} MB"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _check_name(name: str) -> None:
    """Reject entry names that could escape an extraction folder or alias another entry."""
    if not name:
        raise SubmissionError("zip contains an entry with an empty name")
    if "\\" in name:
        raise SubmissionError(f"entry {_q(name)} uses a backslash; use '/' as path separator")
    if "\x00" in name or _CTRL_RE.search(name):
        raise SubmissionError(f"entry {_q(name)} contains control characters")
    if name.startswith("/"):
        raise SubmissionError(f"entry {_q(name)} is an absolute path")
    if _DRIVE_RE.match(name) or ":" in name:
        raise SubmissionError(f"entry {_q(name)} contains a drive letter or ':'")
    parts = name.rstrip("/").split("/")
    if any(p == ".." for p in parts):
        raise SubmissionError(f"entry {_q(name)} contains '..'")
    if any(p in ("", ".") for p in parts):
        raise SubmissionError(f"entry {_q(name)} has an empty or '.' path component")


def _central_directory(path: Path, size: int) -> tuple[int, int] | None:
    """(declared entry count, central-directory bytes) from the end-of-central-directory record,
    located the way ``zipfile`` locates it (zip64 record preferred when present), read BEFORE
    ``zipfile`` parses every entry into memory. None = no record found (zipfile will reject)."""
    if size < _EOCD_SIZE:
        return None
    with open(path, "rb") as fh:
        fh.seek(-_EOCD_SIZE, 2)
        rec = fh.read(_EOCD_SIZE)
        pos = size - _EOCD_SIZE
        if not (rec[:4] == _EOCD_SIG and rec[-2:] == b"\0\0"):
            start = max(size - 2**16 - _EOCD_SIZE, 0)
            fh.seek(start)
            tail = fh.read()
            i = tail.rfind(_EOCD_SIG)
            rec = tail[i:i + _EOCD_SIZE] if i >= 0 else b""
            if len(rec) != _EOCD_SIZE:
                return None
            pos = start + i
        _, _, _, _, n_total, cd_size, _, _ = struct.unpack(_EOCD_FMT, rec)
        if pos >= _LOC64_SIZE + _EOCD64_SIZE:
            fh.seek(pos - _LOC64_SIZE)
            if fh.read(4) == _LOC64_SIG:
                fh.seek(pos - _LOC64_SIZE - _EOCD64_SIZE)
                rec64 = fh.read(_EOCD64_SIZE)
                if rec64[:4] == _EOCD64_SIG:
                    _, _, _, _, _, _, _, n_total, cd_size, _ = struct.unpack(_EOCD64_FMT, rec64)
    return n_total, cd_size


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def validate_meta(meta: dict) -> list[str]:
    """Problems with ``meta.json`` content; an empty list means it is acceptable."""
    if not isinstance(meta, dict):
        return ["meta.json must be a JSON object"]
    problems = [f"meta.json: missing key '{k}'" for k in META_REQUIRED if k not in meta]
    for key in META_REQUIRED:
        if key not in meta:
            continue
        val = meta[key]
        if key == "human_in_loop":
            if not isinstance(val, bool):
                problems.append("meta.json: 'human_in_loop' must be true or false")
            continue
        if key == "agree_to_publish":
            if val is not True:
                problems.append("meta.json: 'agree_to_publish' must be true")
            continue
        if not isinstance(val, str):
            problems.append(f"meta.json: '{key}' must be a string")
            continue
        if key in ("submitter_name", "submission_name"):
            if not val.strip():
                problems.append(f"meta.json: '{key}' must not be empty")
            elif len(val) > NAME_MAX:
                problems.append(f"meta.json: '{key}' is longer than {NAME_MAX} characters")
        elif key == "notes":
            if len(val) > NOTES_MAX:
                problems.append(f"meta.json: 'notes' is longer than {NOTES_MAX} characters")
            continue  # notes may contain line breaks
        elif key == "agent_url":
            if val and not _URL_RE.match(val):
                problems.append("meta.json: 'agent_url' must start with http:// or https://"
                                " (or be empty)")
            elif len(val) > TEXT_MAX:
                problems.append(f"meta.json: 'agent_url' is longer than {TEXT_MAX} characters")
        elif len(val) > TEXT_MAX:
            problems.append(f"meta.json: '{key}' is longer than {TEXT_MAX} characters")
        if _CTRL_RE.search(val):
            problems.append(f"meta.json: '{key}' contains control characters")
    return problems


def _classify(rel: str) -> str | None:
    """Role of a per-task file (path relative to the task folder); None = not allowed."""
    if rel in (STEP_NAME, "features.json", "run.json"):
        return rel
    if _PLAN_RE.match(rel):
        return rel
    if rel.startswith("native/"):
        return "native"
    return None


# ------------------------------------------------------------------------------------ inspect
def inspect_zip(path, task_ids: list[str]) -> Submission:
    """Validate a submission zip WITHOUT extracting it. Raises ``SubmissionError`` when the
    package cannot be scored; recoverable problems go to ``Submission.warnings``."""
    p = Path(path)
    if not p.is_file():
        raise SubmissionError("submission file not found")
    size = p.stat().st_size
    if size > LIMITS["zip_bytes"]:
        raise SubmissionError(f"zip is {_mib(size)}, limit is {_mib(LIMITS['zip_bytes'])}")
    known = set(task_ids)
    try:
        cd = _central_directory(p, size)
    except OSError:
        raise SubmissionError("not a readable zip file (OSError)") from None
    if cd is not None:
        n_declared, cd_bytes = cd
        if n_declared > LIMITS["max_entries"]:
            raise SubmissionError(
                f"zip has {n_declared} entries, limit is {LIMITS['max_entries']}")
        if cd_bytes > LIMITS["max_entries"] * CD_BYTES_PER_ENTRY:
            raise SubmissionError("zip central directory is too large"
                                  f" (more than {LIMITS['max_entries']} entries?)")
    try:
        zf = zipfile.ZipFile(p)
    except _OPEN_ERRORS as exc:
        raise SubmissionError(f"not a readable zip file ({type(exc).__name__})") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > LIMITS["max_entries"]:
            raise SubmissionError(
                f"zip has {len(infos)} entries, limit is {LIMITS['max_entries']}")
        if not infos:
            raise SubmissionError("zip is empty")

        seen: set[str] = set()
        seen_fold: set[str] = set()
        total = 0
        files: dict[str, zipfile.ZipInfo] = {}
        top_dirs: set[str] = set()
        for info in infos:
            name = info.filename
            # orig_filename = the raw name: zipfile turns '\' into '/' on Windows and cuts at NUL
            _check_name(info.orig_filename)
            _check_name(name)
            if name in seen:
                raise SubmissionError(f"duplicate entry {_q(name)}")
            if name.rstrip("/").casefold() in seen_fold:
                raise SubmissionError(f"entry {_q(name)} differs from another only by case")
            seen.add(name)
            seen_fold.add(name.rstrip("/").casefold())
            if info.flag_bits & 0x1:
                raise SubmissionError(f"entry {_q(name)} is encrypted")
            if _is_symlink(info):
                raise SubmissionError(f"entry {_q(name)} is a symbolic link")
            if info.compress_type not in _COMPRESSIONS:
                raise SubmissionError(f"entry {_q(name)} uses an unsupported compression method"
                                      " (use deflate)")
            is_step = name.count("/") == 1 and name.endswith("/" + STEP_NAME)
            if is_step and info.file_size > LIMITS["step_bytes"]:
                raise SubmissionError(f"entry {_q(name)} is {_mib(info.file_size)}, STEP limit"
                                      f" is {_mib(LIMITS['step_bytes'])}")
            if (info.file_size >= RATIO_MIN_BYTES
                    and info.file_size > MAX_RATIO * max(info.compress_size, 1)):
                raise SubmissionError(
                    f"entry {_q(name)} has a compression ratio above {MAX_RATIO}")
            total += info.file_size
            if total > LIMITS["total_uncompressed"]:
                raise SubmissionError(f"zip expands to more than"
                                      f" {_mib(LIMITS['total_uncompressed'])}")

            first, _, rest = name.partition("/")
            if not rest and not name.endswith("/"):  # a top-level file
                if name != "meta.json":
                    raise SubmissionError(_top_level_msg(name, known))
                files[name] = info
                continue
            if first not in known:
                raise SubmissionError(_top_level_msg(first + "/", known))
            top_dirs.add(first)
            if not info.is_dir():
                files[name] = info
        if total >= RATIO_MIN_BYTES and total > MAX_RATIO * max(size, 1):
            raise SubmissionError(f"zip has a compression ratio above {MAX_RATIO}")

        if "meta.json" not in files:
            raise SubmissionError("zip has no top-level meta.json")
        mi = files["meta.json"]
        if mi.file_size > META_MAX_BYTES:
            raise SubmissionError(f"meta.json is larger than {META_MAX_BYTES // 1024} kB")
        raw = _read_bounded(zf, mi, META_MAX_BYTES)
        try:
            # utf-8-sig: Windows PowerShell 5.1 / old Notepad write a BOM
            meta = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError):  # bad UTF-8 / JSON, or absurdly deep nesting
            raise SubmissionError("meta.json is not valid UTF-8 JSON") from None
        problems = validate_meta(meta)
        if problems:
            raise SubmissionError("; ".join(problems))

    warnings: list[str] = []
    extra = sorted(k for k in meta if k not in META_REQUIRED)
    if extra:
        warnings.append("meta.json: unknown keys ignored: " + ", ".join(_q(k, 40) for k in extra))

    tasks: dict[str, dict[str, str]] = {}
    for name in sorted(files):
        if name == "meta.json":
            continue
        tid, _, rel = name.partition("/")
        role = _classify(rel)
        if role is None:
            warnings.append(f"{_q(name)}: not a recognised submission file, ignored")
            continue
        if role == "native":
            continue  # kept in the zip for the Verified layer; not extracted here
        tasks.setdefault(tid, {})[role] = name

    missing: list[str] = []
    for tid in task_ids:
        entry = tasks.get(tid)
        if entry is not None and STEP_NAME in entry:
            continue
        if tid in top_dirs:
            warnings.append(f"task {_q(tid)}: folder has no {STEP_NAME}, counted as missing")
        missing.append(tid)
        tasks.pop(tid, None)
    if not tasks:
        warnings.append(f"no task folder contains {STEP_NAME}; nothing to score")
    ordered = {tid: tasks[tid] for tid in task_ids if tid in tasks}
    return Submission(meta=meta, tasks=ordered, sha256=_sha256(p), missing=missing,
                      warnings=warnings)


def _top_level_msg(name: str, known: set[str]) -> str:
    base = name.rstrip("/")
    if base.casefold() in {k.casefold() for k in known}:
        hint = "task folder names are case-sensitive"
    elif base == "__MACOSX" or base == ".DS_Store":
        hint = "remove macOS metadata"
    else:
        hint = ("top level must be meta.json plus folders named by task ids;"
                " zip the folder contents, not the folder itself")
    return f"unexpected top-level entry {_q(name)} ({hint})"


def _read_bounded(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    try:
        with zf.open(info) as src:
            data = src.read(limit + 1)
    except (*_READ_ERRORS, OSError):
        raise SubmissionError(f"entry {_q(info.filename)} cannot be read (corrupt)") from None
    if len(data) > limit:
        raise SubmissionError(f"entry {_q(info.filename)} is larger than declared")
    return data


# ------------------------------------------------------------------------------------ extract
def extract_task(path, sub: Submission, task_id: str, run_dir) -> Path:
    """Write one task of an inspected submission into ``run_dir``: ``out/output.step`` and, if
    present, ``out/features.json`` and ``plan/re_plan.v<N>.json``. Returns the STEP path.

    Only the entries recorded by ``inspect_zip`` are read, each to a fixed target name, with
    the uncompressed size enforced while streaming. The zip must still hash to ``sub.sha256``.
    """
    entries = sub.tasks.get(task_id)
    if not entries or STEP_NAME not in entries:
        raise SubmissionError(f"task {_q(task_id)} has no {STEP_NAME} in this submission")
    p = Path(path)
    if not p.is_file() or _sha256(p) != sub.sha256:
        raise SubmissionError("submission zip changed since it was inspected")
    run = Path(run_dir)
    targets: list[tuple[str, Path, int]] = []
    for role, arcname in entries.items():
        if role == STEP_NAME:
            targets.append((arcname, run / "out" / STEP_NAME, LIMITS["step_bytes"]))
        elif role == "features.json":
            targets.append((arcname, run / "out" / "features.json", LIMITS["step_bytes"]))
        elif _PLAN_RE.match(role):
            targets.append((arcname, run / "plan" / role.split("/", 1)[1],
                            LIMITS["step_bytes"]))
        # run.json and native/** stay in the zip (not part of the Open scoring input)
    try:
        zf = zipfile.ZipFile(p)
    except _OPEN_ERRORS as exc:
        raise SubmissionError(f"not a readable zip file ({type(exc).__name__})") from None
    done: list[Path] = []
    with zf:
        for arcname, dest, limit in targets:
            tmp = dest.with_name(dest.name + ".part")
            written = 0
            try:
                info = zf.getinfo(arcname)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(tmp, "wb") as dst:
                    while chunk := src.read(_CHUNK):
                        written += len(chunk)
                        if written > limit:
                            raise SubmissionError(f"entry {_q(arcname)} exceeds the size limit")
                        dst.write(chunk)
                shutil.move(str(tmp), str(dest))
                done.append(dest)
            except BaseException as exc:
                # never leave a half-extracted task behind
                for f in (tmp, *done):
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass
                if isinstance(exc, (*_READ_ERRORS, KeyError)):
                    raise SubmissionError(
                        f"entry {_q(arcname)} cannot be read (corrupt)") from None
                if isinstance(exc, OSError):  # host error text may hold host paths: drop it
                    raise SubmissionError(
                        f"entry {_q(arcname)} cannot be written ({type(exc).__name__})") from None
                raise
    return run / "out" / STEP_NAME
