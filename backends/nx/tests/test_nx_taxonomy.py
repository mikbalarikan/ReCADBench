"""CI tests of the NX feature-tree -> taxonomy counter (pure Python, synthetic rows).

Rows mimic what nx_measure.rows() reads from NX 2206 (class names and FeatureType strings as
measured, backends/nx/README.md "Feature mapping"); known ground truth per case.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "nx_taxonomy", HERE.parent / "adapter" / "nx_taxonomy.py"
)
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)


def row(ts, cls, ftype, name=None, parents=(), live=True, layout=None, suppressed=False):
    return {
        "ts": ts,
        "name": name or f"{cls}({ts})",
        "cls": cls,
        "type": ftype,
        "layout": layout,
        "parents": list(parents),
        "live": live,
        "suppressed": suppressed,
    }


CSYS = row(0, "DatumCsys", "DATUM_CSYS")


def counted(doc):
    return [f["taxonomy_type"] for f in doc["features"] if f["counted"]]


def test_conformance_c05_shape():
    doc = T.build(
        [
            CSYS,
            row(1, "SketchFeature", "SKETCH", parents=[0]),
            row(2, "Extrude", "EXTRUDE", parents=[1]),
            row(3, "HolePackage", "HOLE PACKAGE", parents=[2]),
            row(4, "PatternFeature", "Pattern Feature", parents=[3], layout="Linear"),
        ]
    )
    assert counted(doc) == ["extrude", "hole", "pattern_linear"] and doc["count"] == 3
    assert doc["unmapped"] == []


def test_circular_pattern_and_unknown_layout():
    doc = T.build(
        [
            row(1, "Extrude", "EXTRUDE"),
            row(2, "PatternFeature", "Pattern Feature", parents=[1], layout="Circular"),
            row(3, "PatternFeature", "Pattern Feature", parents=[1], layout="Spiral"),
        ]
    )
    assert counted(doc) == ["extrude", "pattern_circular"]
    assert len(doc["unmapped"]) == 1 and "Spiral" in doc["unmapped"][0]


def test_imported_and_unparameterised_bodies_are_unmapped():
    rows = [
        row(1, "Extrude", "EXTRUDE"),
        row(2, "Brep", "BREP", name="Body(2)"),  # Remove Parameters
        row(3, "BodyFeature", "cube", name="cube(3)"),  # STEP import (type = file name)
        row(4, "ExtractFace", "EXTRACT_BODY"),
    ]
    doc = T.build(rows)
    assert counted(doc) == ["extrude"]
    assert len(doc["unmapped"]) == 3
    assert any("unparameterized" in u for u in doc["unmapped"])


def test_single_feature_booleans_are_folded():
    # block + cylinder (unite) + cylinder (subtract): 3 primitives, both booleans folded
    rows = [
        row(1, "Block", "BLOCK"),
        row(2, "Cylinder", "CYLINDER"),
        row(3, "BooleanFeature", "UNITE", parents=[1, 2]),
        row(4, "Cylinder", "CYLINDER"),
        row(5, "BooleanFeature", "SUBTRACT", parents=[1, 4]),
    ]
    doc = T.build(rows)
    assert counted(doc) == ["primitive"] * 3
    assert [f["counted"] for f in doc["features"] if f["taxonomy_type"] == "boolean"] == [
        False,
        False,
    ]


def test_multi_feature_tool_subtract_counts():
    # body: extrude; tool body: extrude + fillet; subtract -> counted (tool has 2 features)
    rows = [
        row(1, "Extrude", "EXTRUDE"),
        row(2, "Extrude", "EXTRUDE"),
        row(3, "EdgeBlend", "BLEND", parents=[2]),
        row(4, "BooleanFeature", "SUBTRACT", parents=[1, 3]),
    ]
    doc = T.build(rows)
    assert counted(doc) == ["extrude", "extrude", "fillet", "boolean"]


def test_union_needs_two_multi_feature_operands():
    body = [row(1, "Extrude", "EXTRUDE"), row(2, "HolePackage", "HOLE PACKAGE", parents=[1])]
    tool = [row(3, "Extrude", "EXTRUDE"), row(4, "Chamfer", "CHAMFER", parents=[3])]
    doc = T.build(body + tool + [row(5, "BooleanFeature", "UNITE", parents=[2, 4])])
    assert doc["count"] == 5
    doc = T.build(
        body
        + [
            row(3, "Mirror", "Mirror Feature", parents=[0, 1, 2]),
            row(4, "BooleanFeature", "UNITE", parents=[2, 3]),
        ]
    )
    assert counted(doc) == ["extrude", "hole", "mirror"]  # conformance C09


def test_dead_and_suppressed_features_are_not_counted():
    rows = [
        row(1, "Extrude", "EXTRUDE"),
        row(2, "Extrude", "EXTRUDE", live=False),
        row(3, "EdgeBlend", "BLEND", suppressed=True),
    ]
    doc = T.build(rows)
    assert doc["count"] == 1 and doc["not_in_final_part"] == ["Extrude(2) (Extrude/EXTRUDE)"]


def test_unknown_class_is_unmapped_never_guessed():
    doc = T.build([row(1, "Extrude", "EXTRUDE"), row(2, "SomethingNew", "NEW_THING")])
    assert doc["count"] == 1 and "unknown NX feature" in doc["unmapped"][0]


def test_every_mapped_type_is_in_the_spec_taxonomy():
    from recadbench_spec.taxonomy import load_taxonomy

    tax = load_taxonomy()
    known = set(tax.known_types)
    mapped = (
        set(T.CLASS_MAP.values())
        | set(T.TYPE_MAP.values())
        | set(T.PATTERN_LAYOUTS.values())
        | set(T.GEOMCOPY_LAYOUTS.values())
        | {"sketch"}
    )
    assert mapped <= known, mapped - known
    assert T.COUNTED == set(tax.counting_rule.counted)


def test_union_body_operand_is_the_target_closure_not_all_earlier_features():
    # Reviewer case (build123d twin gives 4): A = 1 block; B = block + extrude united into B;
    # X = unrelated cylinder; UNITE(A, B); UNITE(., X). A and X are single-feature operands, so
    # both unites are part of their operand. Earlier code counted every feature before the
    # boolean as the body side (n_body 2 for UNITE(6)) and gave 5.
    rows = [
        row(1, "Block", "BLOCK"),
        row(2, "Block", "BLOCK"),
        row(3, "SketchFeature", "SKETCH"),
        row(4, "Extrude", "EXTRUDE", parents=[2, 3]),
        row(5, "Cylinder", "CYLINDER"),
        row(6, "BooleanFeature", "UNITE", parents=[1, 4]),
        row(7, "BooleanFeature", "UNITE", parents=[6, 5]),
    ]
    doc = T.build(rows)
    assert doc["count"] == 4
    u6, u7 = (f for f in doc["features"] if f["taxonomy_type"] == "boolean")
    assert (u6["params"]["n_body"], u6["params"]["n_tool"]) == (1, 2) and not u6["counted"]
    assert (u7["params"]["n_body"], u7["params"]["n_tool"]) == (3, 1) and not u7["counted"]


def test_union_of_two_multi_feature_bodies_after_unrelated_body_counts():
    # unrelated body first must not change the decision either way
    rows = [
        row(1, "Cylinder", "CYLINDER"),
        row(2, "Extrude", "EXTRUDE"),
        row(3, "EdgeBlend", "BLEND", parents=[2]),
        row(4, "Extrude", "EXTRUDE"),
        row(5, "Chamfer", "CHAMFER", parents=[4]),
        row(6, "BooleanFeature", "UNITE", parents=[3, 5]),
    ]
    doc = T.build(rows)
    b = next(f for f in doc["features"] if f["taxonomy_type"] == "boolean")
    assert b["counted"] and (b["params"]["n_body"], b["params"]["n_tool"]) == (2, 2)
