"""C05_rect_pattern: plate 60x40x`plate_t` (8); seed through Hole d=`hole_d` (5) at (-20, -10);
one Pattern Feature (Linear) 3 x 20 along +X, 2 x 20 along +Y.
Features: extrude, hole, pattern_linear = 3 (instances are not features)."""

import _nxr as X

part = X.work()
d = X.Datums(part)
IR = X.ir()  # spec 0.3.0 IR of this case (parameters, pattern params)
X.ir_parameters(part, IR)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 60, 40)
X.finish(sk)
body = X.extrude(part, X.section(part, ls), (0, 0, 1), 0, "plate_t").GetBodies()[0]
seed = X.hole(part, body, [(-20, -10, "plate_t")], "hole_d")
pp = X._ir.feature(IR, "F3")["params"]
f, c = X._ir.formula, X._ir.count_formula
X.pattern_linear(
    part,
    [seed],
    (1, 0, 0),
    c(IR, pp["count"]),
    f(IR, pp["spacing"]),
    (0, 1, 0),
    c(IR, pp["count2"]),
    f(IR, pp["spacing2"]),
)
