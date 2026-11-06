"""C08_shell: block 40x30x20 shelled inward `wall_t` (2), top face open. Features: extrude,
shell = 2."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "wall_t", 2.0)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 40, 30)
X.finish(sk)
body = X.extrude(part, X.section(part, ls), (0, 0, 1), 0, 20).GetBodies()[0]
X.shell(part, body, [X.extreme_plane(body, (0, 0, 1), max)], "wall_t")
