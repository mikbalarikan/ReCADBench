"""Conformance replay C06_circ_pattern: disk + seed hole + 6x circular pattern about Z.

IR: backends/conformance/ir_cases/C06_circ_pattern/re_plan.v1.json
Seed hole F2 at (pcd_r, 0); F3 = PolarLocations context (adapter convention: op inside
PolarLocations = seed feature + pattern_circular, see ../README.md).
"""

from build123d import (
    BuildPart,
    BuildSketch,
    Circle,
    Hole,
    Locations,
    Plane,
    PolarLocations,
    extrude,
)

# Driving dimensions (IR parameters: plate_t, hole_d)
disk_r = 30.0
plate_t = 10.0  # IR parameter
hole_d = 6.0  # IR parameter
pcd_r = 20.0
count = 6
angle_deg = 360.0

with BuildPart() as bp:
    # F1 extrude (new): circle r=disk_r on D1
    with BuildSketch(Plane.XY):
        Circle(disk_r)
    extrude(amount=plate_t)
    # F2 hole (seed at (pcd_r, 0)) + F3 pattern_circular, 6 over 360 deg about D2
    with Locations((0, 0, plate_t)):
        with PolarLocations(pcd_r, count, start_angle=0, angular_range=angle_deg):
            Hole(radius=hole_d / 2)

part = bp.part
