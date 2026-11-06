"""C10_draft_pocket: block 50x40x20; Pocket 30x20 from the top (z = 20) `pocket_depth` deep;
Draft `draft_deg` on the 4 pocket walls, neutral plane = top face, pocket narrowing downwards.

Features: extrude (Pad), extrude (Pocket), draft = 3.
"""

from _fc import bind, faces_where, finish, new_body, params, planar_normal, rect, sketch

doc, body = new_body("C10_draft_pocket")
params(doc, {"pocket_depth": 10.0, "draft_deg": 5.0})
sk = sketch(body, "XY_Plane")
rect(sk, 0, 0, 50, 40)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 20
ps = sketch(body, "XY_Plane", 20.0, "PocketSketch")
rect(ps, 0, 0, 30, 20)
pocket = body.newObject("PartDesign::Pocket", "Pocket")
pocket.Profile = ps
bind(pocket, "Length", "pocket_depth")
doc.recompute()


def wall(f):
    n = planar_normal(f)
    b = f.BoundBox
    return abs(n.z) < 1e-6 and b.XMax <= 15 + 1e-6 and b.XMin >= -15 - 1e-6 \
        and b.YMax <= 10 + 1e-6 and b.YMin >= -10 - 1e-6


top = faces_where(pocket.Shape, lambda f: abs(f.BoundBox.ZMin - 20) < 1e-6
                  and planar_normal(f).z > 0.99)
dr = body.newObject("PartDesign::Draft", "Draft")
dr.Base = (pocket, faces_where(pocket.Shape, wall))
dr.NeutralPlane = (pocket, top)
bind(dr, "Angle", "draft_deg")
dr.Reversed = False
finish(doc, body, dr)
