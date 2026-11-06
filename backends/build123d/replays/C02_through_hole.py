"""Conformance replay C02_through_hole: block + one through hole (extrude, hole).

IR: backends/conformance/ir_cases/C02_through_hole/re_plan.v1.json
"""

from build123d import BuildPart, BuildSketch, Hole, Locations, Plane, Rectangle, extrude

# Driving dimensions (IR parameters: hole_d)
length = 40.0
width = 30.0
height = 20.0
hole_d = 10.0  # IR parameter

with BuildPart() as bp:
    # F1 extrude (new)
    with BuildSketch(Plane.XY):
        Rectangle(length, width)
    extrude(amount=height)
    # F2 hole: through, from the top face at (0, 0)
    with Locations((0, 0, height)):
        Hole(radius=hole_d / 2)

part = bp.part
