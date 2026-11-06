"""Small RE Plan IR helpers with one definition for every consumer (backends, evaluator, tests).

`circular_offsets` is the normative meaning of `pattern_circular` (ADR 0004, spec 0.3.0):
instance k = 0 .. count-1 (k = 0 is the seed) is rotated by ``k * step`` degrees about the
pattern axis, right-hand positive about the axis direction, with

* ``step = angle_deg / count`` when ``angle_deg`` is given (total sweep; 360 = full circle), or
* ``step = pitch_deg`` when ``pitch_deg`` is given (angle between neighbouring instances),
* ``step = 360 / count`` when neither is given (full circle).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FULL_CIRCLE = 360.0


def resolve_number(value: Any, parameters: Mapping[str, float] | None = None) -> float:
    """A param value as a float: plain numbers pass, ``{"ref": name}`` is looked up."""
    if isinstance(value, Mapping):
        if parameters is None or value.get("ref") not in parameters:
            raise KeyError(f"undeclared parameter {value.get('ref')!r}")
        return float(parameters[value["ref"]])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"expected a number or {{'ref'}}, got {value!r}")
    return float(value)


def parameter_values(plan: Mapping[str, Any]) -> dict[str, float]:
    """``{name: value}`` of an IR document's ``parameters``."""
    return {p["name"]: float(p["value"]) for p in plan.get("parameters") or []}


def circular_step(
    params: Mapping[str, Any], parameters: Mapping[str, float] | None = None
) -> float:
    """Angle in degrees between neighbouring instances of a ``pattern_circular``."""
    count = resolve_number(params["count"], parameters)
    if count < 1 or not float(count).is_integer():
        raise ValueError(f"count must be an integer >= 1, got {count}")
    if "angle_deg" in params and "pitch_deg" in params:
        raise ValueError("give at most one of angle_deg / pitch_deg")
    if "pitch_deg" in params:
        return resolve_number(params["pitch_deg"], parameters)
    sweep = resolve_number(params.get("angle_deg", FULL_CIRCLE), parameters)
    return sweep / count


def circular_offsets(
    params: Mapping[str, Any], parameters: Mapping[str, float] | None = None
) -> list[float]:
    """Rotation of each instance relative to the seed, in degrees (first entry is 0)."""
    count = int(resolve_number(params["count"], parameters))
    step = circular_step(params, parameters)
    return [k * step for k in range(count)]


def is_full_circle(
    params: Mapping[str, Any], parameters: Mapping[str, float] | None = None
) -> bool:
    count = resolve_number(params["count"], parameters)
    return abs(abs(circular_step(params, parameters) * count) - FULL_CIRCLE) < 1e-9
