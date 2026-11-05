# ADR 0001 — RE Plan IR `params` values: `oneOf` → `anyOf`

- Status: accepted
- Date: 2026-11-05 (P2)
- Spec version: 0.1.0 (fix lands before the first tag `spec-v0.1.0`; no released version had the bug)

## Context
In `re_plan_ir.schema.json`, `features[].params.additionalProperties` was a `oneOf` over
number | string | boolean | vec3 | `{"ref": name}` | array. A vec3 (`[x, y, z]`) is valid under **both**
the `vec3` branch and the generic `array` branch, so `oneOf` rejected it:
`[10, 20, 5] is valid under each of {'type': 'array'}, {'$ref': '#/$defs/vec3'}`.
Every IR with a vector-valued param (`center`, `size`, `axis`, `direction`, `positions` …) was invalid,
including all realistic plans. Found while writing the P2 round-trip examples.

## Decision
Replace that `oneOf` with `anyOf`. The accepted value set is exactly what the schema intended; only the
"exactly one branch" requirement goes away. No other schema uses an overlapping `oneOf` (sketch entities
use `oneOf` with disjoint `kind` constants, which is correct).

## Consequences
- The fix is part of `spec-v0.1.0`. Because no version was ever tagged with the old text, the
  `spec_version` const stays `0.1.0` instead of bumping to 0.1.1.
- `docs/architecture/contracts/re_plan_ir.schema.json` is updated to the same text (mirror).
- Regression test: `spec/tests/test_ir_rules.py::test_vec3_param_values_are_valid`.
