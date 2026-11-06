"""Conformance replay C09_mirror_bracket: half U-bracket (extrude + hole) mirrored about YZ.

IR: backends/conformance/ir_cases/C09_mirror_bracket/re_plan.v1.json
Seed: L-profile on P_xz (origin (0, depth/2, 0), normal -Y, x_dir +X => sketch y == world +Z),
extruded bracket_depth along -Y; hole through the base; then mirror [F1, F2] about P_yz.
"""

from build123d import BuildPart, BuildSketch, Hole, Locations, Plane, Polygon, extrude, mirror

# Driving dimensions (IR parameters: bracket_depth, hole_d)
half_span = 35.0  # outer half width (x 0..35)
base_t = 6.0
wall_t = 6.0
wall_h = 30.0  # overall height
bracket_depth = 20.0  # IR parameter
hole_d = 6.0  # IR parameter
hole_x = 17.0
hole_y = 0.0

profile = [
    (0, 0),
    (half_span, 0),
    (half_span, wall_h),
    (half_span - wall_t, wall_h),
    (half_span - wall_t, base_t),
    (0, base_t),
]

with BuildPart() as bp:
    # F1 extrude (new): sketch on P_xz, distance bracket_depth along its normal (-Y)
    with BuildSketch(Plane.XZ.offset(-bracket_depth / 2)):
        Polygon(*profile, align=None)
    extrude(amount=bracket_depth)
    # F2 hole: through the base, from its top face (F1:plane:+Z:min) at (hole_x, hole_y)
    with Locations((hole_x, hole_y, base_t)):
        Hole(radius=hole_d / 2)
    # F3 mirror [F1, F2] about P_yz (x = 0)
    mirror(about=Plane.YZ)

part = bp.part
