"""Conformance replay C03_counterbore: block + counterbored through hole (extrude, hole).

IR: backends/conformance/ir_cases/C03_counterbore/re_plan.v1.json
"""

from build123d import BuildPart, BuildSketch, CounterBoreHole, Locations, Plane, Rectangle, extrude

# Driving dimensions (IR parameters: cb_depth)
length = 40.0
width = 40.0
height = 20.0
hole_d = 6.0
cb_d = 12.0
cb_depth = 5.0  # IR parameter

with BuildPart() as bp:
    # F1 extrude (new)
    with BuildSketch(Plane.XY):
        Rectangle(length, width)
    extrude(amount=height)
    # F2 hole kind=counterbore, through, from the top face at (0, 0)
    with Locations((0, 0, height)):
        CounterBoreHole(
            radius=hole_d / 2, counter_bore_radius=cb_d / 2, counter_bore_depth=cb_depth
        )

part = bp.part
