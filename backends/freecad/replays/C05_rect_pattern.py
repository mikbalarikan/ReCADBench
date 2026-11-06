"""C05_rect_pattern: plate 60x40x`plate_t`; seed through Hole d=`hole_d` at (-20, -10);
one LinearPattern of the hole, 3 x 20 along +X and 2 x 20 along +Y (second direction).

Features: extrude, hole, pattern_linear = 3 (instances are not features).
"""

from _fc import bind, circle, finish, new_body, origin, params, rect, sketch

doc, body = new_body("C05_rect_pattern")
params(doc, {"plate_t": 8.0, "hole_d": 5.0})
sk = sketch(body, "XY_Plane")
rect(sk, 0, 0, 60, 40)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
bind(pad, "Length", "plate_t")
hs = sketch(body, "XY_Plane", 8.0, "HoleSketch", offset_expr="Params.plate_t")
circle(hs, -20, -10, 2.5)
doc.recompute()
hole = body.newObject("PartDesign::Hole", "Hole")
hole.Profile = hs
bind(hole, "Diameter", "hole_d")
hole.DepthType = "ThroughAll"
pat = body.newObject("PartDesign::LinearPattern", "LinearPattern")
pat.Originals = [hole]
pat.Direction = (origin(body, "X_Axis"), [""])
pat.Mode = "Spacing"
pat.Offset = 20
pat.Occurrences = 3
pat.Direction2 = (origin(body, "Y_Axis"), [""])
pat.Mode2 = "Spacing"
pat.Offset2 = 20
pat.Occurrences2 = 2
# FreeCAD 26.3 quirk: a pattern created from Python keeps Spacings2 = [0.0] (all second-direction
# copies coincide); -1 is the documented "use Offset2" sentinel, one entry per extra occurrence.
pat.Spacings2 = [-1.0] * (pat.Occurrences2 - 1)
finish(doc, body, pat)
