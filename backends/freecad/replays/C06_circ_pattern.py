"""C06_circ_pattern: disk R30 x `plate_t`; seed through Hole d=`hole_d` at (20, 0);
PolarPattern of the hole, 6 over 360 deg about Z.

Features: extrude, hole, pattern_circular = 3.
"""

from _fc import bind, circle, finish, new_body, origin, params, sketch

doc, body = new_body("C06_circ_pattern")
params(doc, {"plate_t": 10.0, "hole_d": 6.0})
sk = sketch(body, "XY_Plane")
circle(sk, 0, 0, 30)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
bind(pad, "Length", "plate_t")
hs = sketch(body, "XY_Plane", 10.0, "HoleSketch", offset_expr="Params.plate_t")
circle(hs, 20, 0, 3)
doc.recompute()
hole = body.newObject("PartDesign::Hole", "Hole")
hole.Profile = hs
bind(hole, "Diameter", "hole_d")
hole.DepthType = "ThroughAll"
pat = body.newObject("PartDesign::PolarPattern", "PolarPattern")
pat.Originals = [hole]
pat.Axis = (origin(body, "Z_Axis"), [""])
pat.Angle = 360
pat.Occurrences = 6
finish(doc, body, pat)
