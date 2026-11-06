"""C04_revolve_groove: R1 (x 0..10, y 0..`shaft_len`) on P_xz revolved 360 deg about Z;
groove R2 (x 8..12, y 28..32) revolved 360 deg as a cut (PartDesign::Groove).

Features: revolve (Revolution), revolve (Groove) = 2. `shaft_len` drives the sketch constraint
named `shaft_len` (R1 height), bottom-left corner fixed on the origin.
"""

from _fc import Sketcher, finish, new_body, origin, params, rect, sketch

doc, body = new_body("C04_revolve_groove")
params(doc, {"shaft_len": 60.0})
z_axis = origin(body, "Z_Axis")

sk = sketch(body, "XZ_Plane", 0.0, "ShaftSketch")
ids = rect(sk, 5, 30, 10, 60)
sk.addConstraint(Sketcher.Constraint("Coincident", ids[0], 1, -1, 1))  # corner on the origin
sk.addConstraint(Sketcher.Constraint("DistanceX", ids[0], 1, ids[0], 2, 10))
c = sk.addConstraint(Sketcher.Constraint("DistanceY", ids[1], 1, ids[1], 2, 60))
sk.renameConstraint(c, "shaft_len")
sk.setExpression("Constraints.shaft_len", "Params.shaft_len")
rev = body.newObject("PartDesign::Revolution", "Revolution")
rev.Profile = sk
rev.ReferenceAxis = (z_axis, [""])
rev.Angle = 360

gs = sketch(body, "XZ_Plane", 0.0, "GrooveSketch")
rect(gs, 10, 30, 4, 4)
groove = body.newObject("PartDesign::Groove", "Groove")
groove.Profile = gs
groove.ReferenceAxis = (z_axis, [""])
groove.Angle = 360
finish(doc, body, groove)
