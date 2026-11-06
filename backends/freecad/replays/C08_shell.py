"""C08_shell: block 40x30x20 shelled inward (outer skin kept) with `wall_t`, top face open.

Features: extrude, shell (PartDesign::Thickness) = 2.
"""

from _fc import bind, faces_where, finish, new_body, params, planar_normal, rect, sketch

doc, body = new_body("C08_shell")
params(doc, {"wall_t": 2.0})
sk = sketch(body, "XY_Plane")
rect(sk, 0, 0, 40, 30)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 20
doc.recompute()
top = faces_where(pad.Shape, lambda f: abs(f.BoundBox.ZMin - 20) < 1e-6
                  and planar_normal(f).z > 0.99)
th = body.newObject("PartDesign::Thickness", "Thickness")
th.Base = (pad, top)
bind(th, "Value", "wall_t")
th.Mode = "Skin"
th.Join = "Intersection"
th.Reversed = True  # inward: outer dimensions kept (IR convention 3)
finish(doc, body, th)
