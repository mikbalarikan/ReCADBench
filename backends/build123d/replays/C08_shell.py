"""Conformance replay C08_shell: block shelled inward, top face open (extrude, shell).

IR: backends/conformance/ir_cases/C08_shell/re_plan.v1.json
build123d expresses shell as offset(amount=-t, openings=...) inside BuildPart.
"""

from build123d import Axis, BuildPart, BuildSketch, Plane, Rectangle, extrude, offset

# Driving dimensions (IR parameters: wall_t)
length = 40.0
width = 30.0
height = 20.0
wall_t = 2.0  # IR parameter (inward; outer dimensions are kept)

with BuildPart() as bp:
    # F1 extrude (new)
    with BuildSketch(Plane.XY):
        Rectangle(length, width)
    extrude(amount=height)
    # F2 shell: thickness wall_t inward, open face F1:plane:+Z:max
    offset(amount=-wall_t, openings=bp.faces().sort_by(Axis.Z)[-1])

part = bp.part
