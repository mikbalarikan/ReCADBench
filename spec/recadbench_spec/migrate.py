"""Upgrade contract documents of an older spec version to the current one (ADR 0004).

Only ``task`` and ``re_plan_ir`` documents carry ``spec_version``; every other schema is
version-free on disk and needs no migration. ``run_manifest.spec_version`` records which spec a
run pinned and is left untouched.

0.2.0 -> 0.3.0
  * ``spec_version`` -> ``0.3.0`` (task, re_plan_ir).
  * ``pattern_circular.angle_deg`` had no defined meaning in 0.2.0. Its interpretation must be
    stated (``circular_angle``) unless every circular pattern is unambiguous:
      - ``pitch``: angle between neighbouring instances (the R385 pilot GT plans),
      - ``span``: angle from the first to the last instance, 360 = full circle (the 0.2.0
        build123d ``execute-ir`` reading),
      - ``sweep``: the 0.3.0 meaning (total sweep = count * step), kept as is.
    A pattern without ``angle_deg``, or with ``angle_deg: 360``, is a full circle under every
    reading except ``pitch`` (where 360 would stack all instances) and needs no policy.
    The result is written in the 0.3.0 form: a full circle becomes ``angle_deg: 360`` (so a
    ``{"ref"}`` count stays editable), any other pattern becomes ``pitch_deg``. A ``{"ref"}``
    ``angle_deg`` under ``pitch`` stays a ``{"ref"}`` (``pitch_deg: {"ref": same}``); where the
    rewrite cannot keep the binding (full circle, ``span``) a note names the dropped parameter.
  * Typed taxonomy params *narrow* some values that 0.2.0 accepted (any number / list / string
    per key). The mechanical case is migrated: a scalar where the type is ``number_list``
    (e.g. ``primitive.size: 5``) becomes a one-element list. Any other value the 0.3.0 types
    reject (e.g. ``hole.kind: tapped``, a ``{"ref"}`` inside ``hole.positions``) raises
    `MigrationError` naming the param, so it is fixed by hand instead of failing later.
  * Everything else (sketch numbers, features.json keys, backend.yaml ``denied_tools``) only
    *widens* what is allowed.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from .ir import FULL_CIRCLE, parameter_values, resolve_number
from .models import MIGRATABLE_VERSIONS, SPEC_VERSION

CircularAngle = Literal["pitch", "span", "sweep"]
CIRCULAR_ANGLE_POLICIES: tuple[str, ...] = ("pitch", "span", "sweep")
_VERSIONED = ("task", "re_plan_ir")


class MigrationError(ValueError):
    """The document cannot be migrated without more information (or at all)."""


@dataclass
class Migration:
    document: Any
    from_version: str | None
    to_version: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.from_version != self.to_version or bool(self.notes)


def needs_migration(doc: Any, schema: str) -> bool:
    return (
        schema in _VERSIONED
        and isinstance(doc, dict)
        and doc.get("spec_version") in MIGRATABLE_VERSIONS
    )


def migrate_document(
    doc: Any, schema: str, *, circular_angle: CircularAngle | None = None
) -> Migration:
    """Return a migrated deep copy of ``doc`` (the input is never modified).

    Documents already at the current version, version-free schemas and unknown versions are
    returned unchanged (unknown versions then fail schema validation as before).
    """
    if circular_angle is not None and circular_angle not in CIRCULAR_ANGLE_POLICIES:
        raise MigrationError(
            f"circular_angle must be one of {CIRCULAR_ANGLE_POLICIES}, got {circular_angle!r}"
        )
    version = doc.get("spec_version") if isinstance(doc, dict) else None
    if not needs_migration(doc, schema):
        return Migration(doc, version, version)
    out = copy.deepcopy(doc)
    notes: list[str] = []
    if schema == "re_plan_ir":
        notes += _ir_patterns(out, circular_angle)
        notes += _ir_typed_params(out)
    out["spec_version"] = SPEC_VERSION
    notes.insert(0, f"spec_version {version} -> {SPEC_VERSION}")
    return Migration(out, version, SPEC_VERSION, notes)


def _ir_patterns(doc: dict, policy: CircularAngle | None) -> list[str]:
    notes: list[str] = []
    values = parameter_values(doc)
    for i, f in enumerate(doc.get("features") or []):
        if f.get("type") != "pattern_circular":
            continue
        p = f.get("params") or {}
        where = f"features/{i} ({f.get('id')})"
        if "angle_deg" not in p:
            continue  # full circle in every reading
        try:
            angle = resolve_number(p["angle_deg"], values)
            count = resolve_number(p["count"], values)
        except (KeyError, TypeError) as exc:
            raise MigrationError(f"{where}: cannot read count/angle_deg: {exc}") from None
        if not float(count).is_integer() or count < 1:
            raise MigrationError(f"{where}: count must be an integer >= 1, got {count}")
        n = int(count)
        if policy is None:
            if abs(angle - FULL_CIRCLE) < 1e-9:
                continue  # 360 under span/sweep; under pitch it would be degenerate
            raise MigrationError(
                f"{where}: 0.2.0 angle_deg={angle:g} is ambiguous (pitch, span or total sweep); "
                "pass circular_angle='pitch' | 'span' | 'sweep' (CLI: --circular-angle)"
            )
        if policy == "sweep":
            continue
        ref = p["angle_deg"].get("ref") if isinstance(p["angle_deg"], dict) else None
        if policy == "pitch":
            step = angle
        else:  # span: first -> last; 360 means a full circle
            if abs(angle - FULL_CIRCLE) < 1e-9:
                continue
            if n == 1:
                raise MigrationError(f"{where}: span needs count >= 2")
            step = angle / (n - 1)
        if abs(abs(step * n) - FULL_CIRCLE) < 1e-6:
            new = {"angle_deg": 360 if step > 0 else -360}
            text = f"angle_deg {angle:g} ({policy}) -> angle_deg {new['angle_deg']:g} (full circle)"
        else:
            if abs(step * n) > FULL_CIRCLE + 1e-6:
                raise MigrationError(
                    f"{where}: {n} instances at {step:g} deg overlap (more than a full turn)"
                )
            if ref is not None and policy == "pitch":
                new = {"pitch_deg": {"ref": ref}}
                text = f"angle_deg {{ref: {ref}}} = {angle:g} (pitch) -> pitch_deg {{ref: {ref}}}"
                ref = None  # binding kept
            else:
                new = {"pitch_deg": _clean(step)}
                text = f"angle_deg {angle:g} ({policy}) -> pitch_deg {new['pitch_deg']:g}"
        if ref is not None:
            text += f"; binding to parameter {ref!r} dropped (re-bind it by hand if needed)"
        f["params"] = {k: v for k, v in p.items() if k != "angle_deg"}
        f["params"].update(new)
        notes.append(f"{where}: {text}")
    return notes


def _ir_typed_params(doc: dict) -> list[str]:
    """Wrap scalar ``number_list`` values; raise on any other value the 0.3.0 types reject."""
    from .taxonomy import load_taxonomy
    from .validate import _validator  # late import: validate imports this module

    tax = load_taxonomy()
    notes: list[str] = []
    for i, f in enumerate(doc.get("features") or []):
        t, params = f.get("type"), f.get("params")
        if t not in tax.known_types or not isinstance(params, dict):
            continue
        for name, value in params.items():
            if name not in tax.allowed_params(t):
                continue
            scalar = isinstance(value, (int, float)) and not isinstance(value, bool)
            if tax.param_type(t, name) == "number_list" and (
                scalar or (isinstance(value, dict) and set(value) == {"ref"})
            ):
                params[name] = [value]
                notes.append(
                    f"features/{i} ({f.get('id')}): {name} {json.dumps(value)} -> "
                    f"[{json.dumps(value)}] (0.3.0 type number_list)"
                )
    bad = sorted(
        {
            f"/{'/'.join(map(str, e.absolute_path))}: {e.message}"
            for e in _validator("re_plan_ir").iter_errors(doc)
            if len(e.absolute_path) >= 4
            and list(e.absolute_path)[0] == "features"
            and list(e.absolute_path)[2] == "params"
        }
    )
    if bad:
        raise MigrationError(
            "0.2.0 param values that the 0.3.0 typed taxonomy params reject (fix by hand): "
            + "; ".join(bad)
        )
    return notes


def _clean(x: float) -> float | int:
    r = round(x, 9)
    return int(r) if float(r).is_integer() else r
