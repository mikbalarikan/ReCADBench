"""Conformance replay C10_draft_pocket: block, rectangular pocket cut, draft on pocket walls.

IR: backends/conformance/ir_cases/C10_draft_pocket/re_plan.v1.json
Draft: neutral = top face (z = height), pull direction +Z; the pocket keeps its size at the
opening and narrows toward the floor by pocket_depth * tan(draft_deg) per wall.
"""

from build123d import Axis, BuildPart, BuildSketch, Mode, Plane, Rectangle, draft, extrude

# Driving dimensions (IR parameters: pocket_depth, draft_deg)
length = 50.0
width = 40.0
height = 20.0
pocket_l = 30.0
pocket_w = 20.0
pocket_depth = 10.0  # IR parameter
draft_deg = 5.0  # IR parameter

with BuildPart() as bp:
    # F1 extrude (new)
    with BuildSketch(Plane.XY):
        Rectangle(length, width)
    extrude(amount=height)
    # F2 extrude (cut): pocket sketched on P_top (z = height), depth pocket_depth along -Z
    with BuildSketch(Plane.XY.offset(height)):
        Rectangle(pocket_l, pocket_w)
    extrude(amount=pocket_depth, dir=(0, 0, -1), mode=Mode.SUBTRACT)
    # F3 draft: the 4 pocket walls (vertical planar faces strictly inside the outline)
    walls = [
        f
        for f in bp.faces().filter_by(Axis.Z, reverse=True)
        if abs(f.center().X) < length / 2 - 1e-6 and abs(f.center().Y) < width / 2 - 1e-6
    ]
    draft(walls, neutral_plane=Plane.XY.offset(height), angle=draft_deg)

part = bp.part
