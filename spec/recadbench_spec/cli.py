"""`recadbench-spec` command line.

    recadbench-spec validate FILE... [--schema NAME] [--json] [--strict]
    recadbench-spec schemas                      # list schema names + detected file names
    recadbench-spec compile-schema NAME          # print the schema used for validation
    recadbench-spec taxonomy                     # print feature types + counting rule

Exit codes: 0 all valid · 1 at least one invalid file (or warnings with --strict) · 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import __version__
from .taxonomy import load_taxonomy
from .validate import _BASENAMES, SCHEMA_NAMES, compiled_schema, detect_schema, validate_file


def _validate(args: argparse.Namespace) -> int:
    reports, rc = [], 0
    for path in args.files:
        schema = args.schema or detect_schema(path)
        if schema is None:
            print(f"{path}: cannot detect schema from the file name; use --schema", file=sys.stderr)
            return 2
        try:
            report = validate_file(path, schema)
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recadbench-spec", description="ReCADBench contract tools")
    p.add_argument("--version", action="version", version=f"recadbench-spec {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate JSON/YAML files against the contracts")
    v.add_argument("files", nargs="+")
    v.add_argument("--schema", choices=SCHEMA_NAMES, help="override schema detection")
    v.add_argument("--json", action="store_true", help="machine-readable output")
    v.add_argument("--strict", action="store_true", help="treat warnings as failures")
    v.set_defaults(func=_validate)
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
