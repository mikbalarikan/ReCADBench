"""C01_block: rect 40x30 on D1 (XY), extrude `height` = 20 along +Z. Features: extrude = 1."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "height", 20.0)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 40, 30)
X.finish(sk)
X.extrude(part, X.section(part, ls), (0, 0, 1), 0, "height")
