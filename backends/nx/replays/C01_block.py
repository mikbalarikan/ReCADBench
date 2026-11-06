"""C01_block: rect 40x30 on D1 (XY), extrude `height` = 20 along +Z. Features: extrude = 1."""

import _nxr as X

part = X.work()
d = X.Datums(part)
IR = X.ir()  # spec 0.3.0 IR of this case (parameters, pattern params)
X.ir_parameters(part, IR)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 40, 30)
X.finish(sk)
X.extrude(part, X.section(part, ls), (0, 0, 1), 0, "height")
