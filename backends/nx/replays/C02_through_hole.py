"""C02_through_hole: block 40x30x20; through Hole d=`hole_d` (10) at (0, 0) from the top face.
Features: extrude, hole = 2 (the cut of the hole is part of the HolePackage)."""

import _nxr as X

part = X.work()
d = X.Datums(part)
IR = X.ir()  # spec 0.3.0 IR of this case (parameters, pattern params)
X.ir_parameters(part, IR)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 40, 30)
X.finish(sk)
body = X.extrude(part, X.section(part, ls), (0, 0, 1), 0, 20).GetBodies()[0]
X.hole(part, body, [(0, 0, 20)], "hole_d")
