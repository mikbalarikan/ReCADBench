"""Conformance replay C07_fillet_chamfer: block, fillet top edges, chamfer bottom edges.

IR: backends/conformance/ir_cases/C07_fillet_chamfer/re_plan.v1.json
"""

from build123d import Axis, BuildPart, BuildSketch, Plane, Rectangle, chamfer, extrude, fillet

# Driving dimensions (IR parameters: fillet_r, chamfer_d)
length = 40.0
width = 30.0
height = 20.0
fillet_r = 4.0  # IR parameter
chamfer_d = 2.0  # IR parameter

with BuildPart() as bp:
    # F1 extrude (new)
    with BuildSketch(Plane.XY):
        Rectangle(length, width)
    extrude(amount=height)
    # F2 fillet: the 4 boundary edges of the top face (F1:plane:+Z:max)
    fillet(bp.faces().sort_by(Axis.Z)[-1].edges(), radius=fillet_r)
    # F3 chamfer: the 4 boundary edges of the bottom face (F1:plane:-Z:min)
    chamfer(bp.faces().sort_by(Axis.Z)[0].edges(), length=chamfer_d)

part = bp.part
