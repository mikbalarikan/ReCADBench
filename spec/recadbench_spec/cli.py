"""`recadbench-spec` command line.

    recadbench-spec validate FILE... [--schema NAME] [--json] [--strict] [--migrate]
    recadbench-spec migrate FILE... [--circular-angle pitch|span|sweep] [--write | -o OUT]
    recadbench-spec schemas                      # list schema names + detected file names
    recadbench-spec compile-schema NAME          # print the schema used for validation
    recadbench-spec taxonomy                     # print feature types + counting rule

Exit codes: 0 all valid · 1 at least one invalid file (or warnings with --strict) · 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .migrate import CIRCULAR_ANGLE_POLICIES, MigrationError, migrate_document
from .taxonomy import load_taxonomy
from .validate import (
    _BASENAMES,
    SCHEMA_NAMES,
    compiled_schema,
    detect_schema,
    load_document,
    validate_document,
    validate_file,
)


def _validate(args: argparse.Namespace) -> int:
    reports, rc = [], 0
    for path in args.files:
        schema = args.schema or detect_schema(path)
        if schema is None:
            print(f"{path}: cannot detect schema from the file name; use --schema", file=sys.stderr)
            return 2
        try:
            report = validate_file(
                path, schema, migrate=args.migrate, circular_angle=args.circular_angle
            )
        except (OSError, ValueError) as exc:  # unreadable file, JSON/YAML syntax error
            print(f"{path}: {exc}", file=sys.stderr)
            rc = 1
            continue
        reports.append(report)
        if not report.ok or (args.strict and report.warnings):
            rc = 1
        if not args.json:
            status = "OK" if report.ok else "INVALID"
            print(f"{path}: {status} ({schema})")
            for issue in report.issues:
                print(f"  {issue}")
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False))
    return rc


_YAML_VERSION_LINE = re.compile(r"^spec_version:[ \t]*['\"]?([0-9.]+)['\"]?[ \t]*$", re.M)


def _render(path: Path, original: str, doc: Any) -> str:
    """Serialise a migrated document in the style of the original file (LF line ends)."""
    if path.suffix.lower() == ".json":
        text = json.dumps(doc, indent=2, ensure_ascii=False)
        return text + "\n" if original.endswith("\n") else text
    # YAML: only rewrite the spec_version line so comments / layout survive
    text = _YAML_VERSION_LINE.sub(f"spec_version: {doc['spec_version']}", original, count=1)
    if yaml.safe_load(text) == doc:
        return text
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _migrate(args: argparse.Namespace) -> int:
    if args.out and len(args.files) != 1:
        print("--out needs exactly one input file", file=sys.stderr)
        return 2
    rc = 0
    for name in args.files:
        path = Path(name)
        schema = args.schema or detect_schema(path)
        if schema is None:
            print(f"{path}: cannot detect schema from the file name; use --schema", file=sys.stderr)
            return 2
        try:
            original = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            doc = load_document(path)
            mig = migrate_document(doc, schema, circular_angle=args.circular_angle)
        except (OSError, ValueError) as exc:  # includes MigrationError
            tag = "CANNOT MIGRATE" if isinstance(exc, MigrationError) else "ERROR"
            print(f"{path}: {tag}: {exc}", file=sys.stderr)
            rc = 1
            continue
        report = validate_document(mig.document, schema, source=path)
        if not mig.changed:
            print(f"{path}: up to date (spec {mig.from_version or 'n/a'})")
        else:
            print(f"{path}: {mig.from_version} -> {mig.to_version}")
            for note in mig.notes:
                print(f"  {note}")
        for issue in report.issues:
            print(f"  {issue}")
        if not report.ok:
            print(f"{path}: migrated document is INVALID, nothing written", file=sys.stderr)
            rc = 1
            continue
        if not mig.changed:
            continue
        text = _render(path, original, mig.document)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        elif args.write:
            path.write_text(text, encoding="utf-8", newline="\n")
        else:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return rc


def _schemas(_: argparse.Namespace) -> int:
    by_schema: dict[str, list[str]] = {n: [] for n in SCHEMA_NAMES}
    for base, name in _BASENAMES.items():
        by_schema[name].append(base)
    by_schema["re_plan_ir"].insert(0, "re_plan.vN.json")
    for name, files in by_schema.items():
        print(f"{name:18} {', '.join(files)}")
    return 0


def _compile(args: argparse.Namespace) -> int:
    print(json.dumps(compiled_schema(args.name), indent=2, ensure_ascii=False))
    return 0


def _taxonomy(_: argparse.Namespace) -> int:
    tax = load_taxonomy()
    for t, spec in tax.types.items():
        sk = " (sketch)" if spec.needs_sketch else ""
        print(f"{t:18} counted{sk}: {', '.join(spec.params)}")
    print(f"{'not counted':18} {', '.join(tax.counting_rule.not_counted)}")
    print(f"{'face types':18} {', '.join(tax.face_types)}")
    return 0


_CIRC_HELP = (
    "meaning of a 0.2.0 pattern_circular.angle_deg: pitch (between instances), span (first to "
    "last) or sweep (the 0.3.0 total sweep)"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recadbench-spec", description="ReCADBench contract tools")
    p.add_argument("--version", action="version", version=f"recadbench-spec {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate JSON/YAML files against the contracts")
    v.add_argument("files", nargs="+")
    v.add_argument("--schema", choices=SCHEMA_NAMES, help="override schema detection")
    v.add_argument("--json", action="store_true", help="machine-readable output")
    v.add_argument("--strict", action="store_true", help="treat warnings as failures")
    v.add_argument(
        "--migrate", action="store_true", help="upgrade older-spec documents in memory first"
    )
    v.add_argument("--circular-angle", choices=CIRCULAR_ANGLE_POLICIES, help=_CIRC_HELP)
    v.set_defaults(func=_validate)
    m = sub.add_parser("migrate", help="upgrade documents of an older spec version")
    m.add_argument("files", nargs="+")
    m.add_argument("--schema", choices=SCHEMA_NAMES, help="override schema detection")
    m.add_argument("--circular-angle", choices=CIRCULAR_ANGLE_POLICIES, help=_CIRC_HELP)
    g = m.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help="rewrite the files in place")
    g.add_argument("-o", "--out", help="write the (single) migrated file here")
    m.set_defaults(func=_migrate)
    sub.add_parser(
        "schemas", help="list schemas and the file names they are detected from"
    ).set_defaults(func=_schemas)
    c = sub.add_parser("compile-schema", help="print the schema used for validation")
    c.add_argument("name", choices=SCHEMA_NAMES)
    c.set_defaults(func=_compile)
    sub.add_parser("taxonomy", help="print feature types and counting rule").set_defaults(
        func=_taxonomy
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
