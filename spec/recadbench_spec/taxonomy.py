"""Feature taxonomy loader + counting rule (`feature_taxonomy.yaml`).

The taxonomy is the single list of canonical feature types. Everything type-related in the spec
is derived from it:

* the RE Plan IR ``features[].type`` enum (`feature_type_enum`, and the schema's enum is checked
  against it by `check_ir_schema_sync`),
* the per-type allowed ``params`` keys, their value types (spec 0.3.0) and the ``needs_sketch``
  requirement, injected into the compiled IR schema (`recadbench_spec.validate.compiled_schema`),
* the counting rule used for S2 parsimony (`Taxonomy.count`).
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TAXONOMY_FILE = "feature_taxonomy.yaml"


class EnumType(BaseModel):
    """A param type that only allows the listed strings (`{enum: [...]}` in the YAML)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enum: tuple[str, ...] = Field(min_length=1)


ParamType = str | EnumType


class TypeSpec(BaseModel):
    """One canonical feature type. ``params`` maps each allowed param name to its type
    (spec 0.3.0, ADR 0004): a ``param_types`` name or an :class:`EnumType`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    params: dict[str, ParamType]
    needs_sketch: bool
    note: str | None = None


class CountingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    counted: tuple[str, ...]
    not_counted: tuple[str, ...]
    notes: tuple[str, ...] = ()


class Taxonomy(BaseModel):
    """Parsed `feature_taxonomy.yaml`, with self-consistency checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    counting_rule: CountingRule
    param_types: dict[str, str] = Field(min_length=1)
    types: dict[str, TypeSpec]
    face_types: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Taxonomy:
        counted = set(self.counting_rule.counted)
        not_counted = set(self.counting_rule.not_counted)
        if overlap := counted & not_counted:
            raise ValueError(f"types both counted and not_counted: {sorted(overlap)}")
        if counted != set(self.types):
            raise ValueError(
                "counting_rule.counted must list exactly the keys of types: "
                f"missing {sorted(set(self.types) - counted)}, "
                f"extra {sorted(counted - set(self.types))}"
            )
        for name, spec in self.types.items():
            for pname, ptype in spec.params.items():
                if isinstance(ptype, str) and ptype not in self.param_types:
                    raise ValueError(
                        f"type {name!r} param {pname!r}: unknown param type {ptype!r}; "
                        f"known: {sorted(self.param_types)}"
                    )
        return self

    # -- queries ---------------------------------------------------------------------------
    @property
    def feature_types(self) -> tuple[str, ...]:
        """IR feature types, in taxonomy order."""
        return tuple(self.types)

    @property
    def known_types(self) -> frozenset[str]:
        """Every type an adapter may report: counted and not-counted."""
        return frozenset(self.counting_rule.counted) | frozenset(self.counting_rule.not_counted)

    def allowed_params(self, feature_type: str) -> tuple[str, ...]:
        return tuple(self._spec(feature_type).params)

    def param_type(self, feature_type: str, param: str) -> ParamType:
        """Declared type of one param (``KeyError`` if the param is not allowed)."""
        try:
            return self._spec(feature_type).params[param]
        except KeyError:
            raise KeyError(f"param {param!r} is not allowed for type {feature_type!r}") from None

    def needs_sketch(self, feature_type: str) -> bool:
        return self._spec(feature_type).needs_sketch

    def is_counted(self, taxonomy_type: str) -> bool:
        """Counting rule: True for counted types, False for not-counted; unknown types raise."""
        if taxonomy_type in self.counting_rule.counted:
            return True
        if taxonomy_type in self.counting_rule.not_counted:
            return False
        raise KeyError(f"unknown taxonomy type {taxonomy_type!r}")

    def count(self, taxonomy_types: Iterable[str]) -> int:
        """Feature count of a sequence of native commands mapped to taxonomy types.

        Each counted item is one feature regardless of how many edges/faces/instances it touches;
        a pattern/mirror counts 1 and its seed counts separately. Unknown types raise ``KeyError``
        (adapters must report non-parametric items as ``unmapped`` instead).
        """
        return sum(1 for t in taxonomy_types if self.is_counted(t))

    def _spec(self, feature_type: str) -> TypeSpec:
        try:
            return self.types[feature_type]
        except KeyError:
            raise KeyError(f"unknown feature type {feature_type!r}") from None


def _read(path: Path | None) -> dict[str, Any]:
    if path is not None:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    text = resources.files("recadbench_spec").joinpath(TAXONOMY_FILE).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def load_taxonomy(path: str | Path | None = None) -> Taxonomy:
    """Load and check a taxonomy file (default: the packaged `feature_taxonomy.yaml`)."""
    if path is None:
        return _packaged()
    return Taxonomy.model_validate(_read(Path(path)))


@lru_cache(maxsize=1)
def _packaged() -> Taxonomy:
    return Taxonomy.model_validate(_read(None))


def feature_type_enum(taxonomy: Taxonomy | None = None) -> type[enum.Enum]:
    """`FeatureType` str-enum generated from the taxonomy (member name == value)."""
    tax = taxonomy or load_taxonomy()
    return enum.Enum("FeatureType", {t: t for t in tax.feature_types}, type=str)


def check_ir_schema_sync(ir_schema: dict[str, Any], taxonomy: Taxonomy | None = None) -> list[str]:
    """Differences between the IR schema's `type` enum and the taxonomy (empty = in sync)."""
    tax = taxonomy or load_taxonomy()
    enum_ = ir_schema["properties"]["features"]["items"]["properties"]["type"]["enum"]
    problems = []
    if missing := [t for t in tax.feature_types if t not in enum_]:
        problems.append(f"IR schema type enum is missing taxonomy types {missing}")
    if extra := [t for t in enum_ if t not in tax.types]:
        problems.append(f"IR schema type enum has types not in the taxonomy {extra}")
    return problems
