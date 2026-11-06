"""Conformance replay C01_block: rectangular block (1 extrude).

IR: backends/conformance/ir_cases/C01_block/re_plan.v1.json
"""

from build123d import BuildPart, BuildSketch, Plane, Rectangle, extrude

# Driving dimensions (IR parameters: height)
length = 40.0  # sketch literal in the IR
width = 30.0  # sketch literal in the IR
height = 20.0  # IR parameter

with BuildPart() as bp:
    # F1 extrude (new): rect on D1, +Z
    with BuildSketch(Plane.XY):
        Rectangle(length, width)
    extrude(amount=height)

part = bp.part
