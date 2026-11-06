"""C03_counterbore: block 40x40x20; ONE counterbored through Hole d6, cb d12 x `cb_depth` (5).
Features: extrude, hole = 2 (the counterbore is a form of the hole, not a second cut)."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "cb_depth", 5.0)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 40, 40)
X.finish(sk)
body = X.extrude(part, X.section(part, ls), (0, 0, 1), 0, 20).GetBodies()[0]
X.hole(part, body, [(0, 0, 20)], 6, cbore=(12, "cb_depth"))
