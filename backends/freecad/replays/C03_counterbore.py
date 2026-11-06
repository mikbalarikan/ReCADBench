"""C03_counterbore: block 40x40x20 + one counterbored Hole (d6 through, cb d12 x `cb_depth`).

Features: extrude, hole (native counterbore = parameter of the one Hole feature) = 2.
"""

from _fc import bind, circle, finish, new_body, params, rect, sketch

doc, body = new_body("C03_counterbore")
params(doc, {"cb_depth": 5.0})
sk = sketch(body, "XY_Plane")
rect(sk, 0, 0, 40, 40)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 20
hs = sketch(body, "XY_Plane", 20.0, "HoleSketch")
circle(hs, 0, 0, 3)
doc.recompute()
hole = body.newObject("PartDesign::Hole", "Hole")
hole.Profile = hs
hole.Diameter = 6
hole.DepthType = "ThroughAll"
hole.HoleCutType = "Counterbore"
hole.HoleCutDiameter = 12
bind(hole, "HoleCutDepth", "cb_depth")
finish(doc, body, hole)
