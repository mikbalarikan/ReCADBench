"""C09_mirror_bracket: half L-profile on P_xz at y = +10, padded `bracket_depth` along -Y;
through Hole d=`hole_d` at (17, 0) from the base top (z = 6); Mirrored of [Pad, Hole] about YZ.

Features: extrude, hole, mirror = 3 (the merge of the mirrored copy is part of the mirror).
"""

from _fc import bind, circle, finish, new_body, origin, params, polygon, sketch

doc, body = new_body("C09_mirror_bracket")
params(doc, {"bracket_depth": 20.0, "hole_d": 6.0})
# XZ_Plane normal is -Y, so a -10 offset along the normal puts the sketch at world y = +10
sk = sketch(body, "XZ_Plane", -10.0, "ProfileSketch")
polygon(sk, [(0, 0), (35, 0), (35, 30), (29, 30), (29, 6), (0, 6)])
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
bind(pad, "Length", "bracket_depth")  # along the sketch normal (-Y)
hs = sketch(body, "XY_Plane", 6.0, "HoleSketch")
circle(hs, 17, 0, 3)
doc.recompute()
hole = body.newObject("PartDesign::Hole", "Hole")
hole.Profile = hs
bind(hole, "Diameter", "hole_d")
hole.DepthType = "ThroughAll"
mir = body.newObject("PartDesign::Mirrored", "Mirrored")
mir.Originals = [pad, hole]
mir.MirrorPlane = (origin(body, "YZ_Plane"), [""])
finish(doc, body, mir)
