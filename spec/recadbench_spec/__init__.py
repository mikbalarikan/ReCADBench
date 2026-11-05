"""recadbench-spec — ReCADBench contracts: JSON Schemas, feature taxonomy, models, validation.

from recadbench_spec import validate_file, load_taxonomy, RePlan
report = validate_file("plan/re_plan.v1.json")
assert report.ok, report.errors
"""

from .models import MODELS, SPEC_VERSION, FeatureType, RePlan
from .taxonomy import Taxonomy, load_taxonomy
from .validate import (
    SCHEMA_NAMES,
    Issue,
    Report,
    compiled_schema,
    detect_schema,
    load_schema,
    validate_document,
    validate_file,
)

__version__ = SPEC_VERSION

__all__ = [
    "MODELS",
    "SCHEMA_NAMES",
    "SPEC_VERSION",
    "FeatureType",
    "Issue",
    "RePlan",
    "Report",
    "Taxonomy",
    "compiled_schema",
    "detect_schema",
    "load_schema",
    "load_taxonomy",
    "validate_document",
    "validate_file",
    "__version__",
]
