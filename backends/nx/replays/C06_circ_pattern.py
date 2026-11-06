"""C06_circ_pattern: disk R30 x `plate_t` (10); seed through Hole d=`hole_d` (6) at (20, 0);
one Pattern Feature (Circular) of 6 over 360 deg about Z (first instance = the seed).
Features: extrude, hole, pattern_circular = 3."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "plate_t", 10.0)
X.expr(part, "hole_d", 6.0)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
arc = X.circle(part, sk, d.origin, 60)
X.finish(sk)
body = X.extrude(part, X.section(part, arc), (0, 0, 1), 0, "plate_t").GetBodies()[0]
seed = X.hole(part, body, [(20, 0, "plate_t")], "hole_d")
X.pattern_circular(part, [seed], (0, 0, 0), (0, 0, 1), 6, 360)
