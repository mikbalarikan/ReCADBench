"""C07_fillet_chamfer: block 40x30x20; one Fillet R`fillet_r` on the 4 edges of the top face;
one Chamfer `chamfer_d` (equal distance) on the 4 edges of the bottom face.

Features: extrude, fillet, chamfer = 3 (one command each, however many edges).
"""

from _fc import bind, edges_where, finish, new_body, params, rect, sketch

doc, body = new_body("C07_fillet_chamfer")
params(doc, {"fillet_r": 4.0, "chamfer_d": 2.0})
sk = sketch(body, "XY_Plane")
rect(sk, 0, 0, 40, 30)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 20
doc.recompute()


def flat_at(z):
    def pred(e):
        b = e.BoundBox
        return abs(b.ZMin - z) < 1e-6 and abs(b.ZMax - z) < 1e-6
    return pred


fil = body.newObject("PartDesign::Fillet", "Fillet")
fil.Base = (pad, edges_where(pad.Shape, flat_at(20.0)))
bind(fil, "Radius", "fillet_r")
doc.recompute()
cha = body.newObject("PartDesign::Chamfer", "Chamfer")
cha.Base = (fil, edges_where(fil.Shape, flat_at(0.0)))
cha.ChamferType = "Equal distance"
bind(cha, "Size", "chamfer_d")
finish(doc, body, cha)
