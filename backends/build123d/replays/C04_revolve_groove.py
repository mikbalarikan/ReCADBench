"""Conformance replay C04_revolve_groove: revolved shaft + revolved-cut groove (revolve x2).

IR: backends/conformance/ir_cases/C04_revolve_groove/re_plan.v1.json
Sketches lie on P_xz (origin 0, normal -Y, x_dir +X => sketch y == world +Z) == build123d Plane.XZ.
"""

from build123d import Axis, BuildPart, BuildSketch, Locations, Mode, Plane, Rectangle, revolve

# Driving dimensions (IR parameters: shaft_len)
shaft_r = 10.0
shaft_len = 60.0  # IR parameter (bound to the height of sketch rect R1, bottom on z=0)
groove_z = 30.0  # groove centre height
groove_w = 4.0
groove_depth = 2.0

with BuildPart() as bp:
    # F1 revolve (new): rect x 0..shaft_r, z 0..shaft_len, 360 deg about D2 (Z axis)
    with BuildSketch(Plane.XZ):
        with Locations((shaft_r / 2, shaft_len / 2)):
            Rectangle(shaft_r, shaft_len)
    revolve(axis=Axis.Z, revolution_arc=360)
    # F2 revolve (cut): groove rect x (shaft_r - depth)..(shaft_r + depth), width groove_w
    with BuildSketch(Plane.XZ):
        with Locations((shaft_r, groove_z)):
            Rectangle(2 * groove_depth, groove_w)
    revolve(axis=Axis.Z, revolution_arc=360, mode=Mode.SUBTRACT)

part = bp.part
