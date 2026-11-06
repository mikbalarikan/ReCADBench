"""C02_through_hole: block 40x30x20 + through Hole d=`hole_d` at (0, 0) from the top face.

Features: extrude (Pad), hole (PartDesign::Hole, through all) = 2.
"""

from _fc import bind, circle, finish, new_body, params, rect, sketch

doc, body = new_body("C02_through_hole")
params(doc, {"hole_d": 10.0})
sk = sketch(body, "XY_Plane")
rect(sk, 0, 0, 40, 30)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 20
# hole centres on the entry face (z = 20); PartDesign::Hole drills against the sketch normal
hs = sketch(body, "XY_Plane", 20.0, "HoleSketch")
circle(hs, 0, 0, 5)
doc.recompute()
hole = body.newObject("PartDesign::Hole", "Hole")
hole.Profile = hs
bind(hole, "Diameter", "hole_d")
hole.DepthType = "ThroughAll"
finish(doc, body, hole)
