"""Conformance replay C05_rect_pattern: plate + seed hole + 3x2 linear pattern.

IR: backends/conformance/ir_cases/C05_rect_pattern/re_plan.v1.json
build123d has no "pattern" command: the IR pattern_linear F3 of seed hole F2 is expressed as the
seed operation executed under a GridLocations context (adapter convention: op inside
GridLocations = seed feature + pattern_linear, see ../README.md).
"""

from build123d import (
    BuildPart,
    BuildSketch,
    GridLocations,
    Hole,
    Locations,
    Plane,
    Rectangle,
    extrude,
)

# Driving dimensions (IR parameters: plate_t, hole_d)
length = 60.0
width = 40.0
plate_t = 8.0  # IR parameter
hole_d = 5.0  # IR parameter
spacing = 20.0
count = 3
spacing2 = 20.0
count2 = 2

with BuildPart() as bp:
    # F1 extrude (new)
    with BuildSketch(Plane.XY):
        Rectangle(length, width)
    extrude(amount=plate_t)
    # F2 hole (seed at (-20, -10)) + F3 pattern_linear 3 x 2 (grid centred on the origin)
    with Locations((0, 0, plate_t)):
        with GridLocations(spacing, spacing2, count, count2):
            Hole(radius=hole_d / 2)

part = bp.part
