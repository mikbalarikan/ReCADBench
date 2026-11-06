"""C05_rect_pattern: plate 60x40x`plate_t` (8); seed through Hole d=`hole_d` (5) at (-20, -10);
one Pattern Feature (Linear) 3 x 20 along +X, 2 x 20 along +Y.
Features: extrude, hole, pattern_linear = 3 (instances are not features)."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "plate_t", 8.0)
X.expr(part, "hole_d", 5.0)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 60, 40)
X.finish(sk)
body = X.extrude(part, X.section(part, ls), (0, 0, 1), 0, "plate_t").GetBodies()[0]
seed = X.hole(part, body, [(-20, -10, "plate_t")], "hole_d")
X.pattern_linear(part, [seed], (1, 0, 0), 3, 20, (0, 1, 0), 2, 20)
