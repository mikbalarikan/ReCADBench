"""C07_fillet_chamfer: block 40x30x20; ONE Edge Blend R`fillet_r` (4) on the 4 edges of the top
face; ONE Chamfer `chamfer_d` (2) on the 4 edges of the bottom face. Features: extrude, fillet,
chamfer = 3."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "fillet_r", 4.0)
X.expr(part, "chamfer_d", 2.0)
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 40, 30)
X.finish(sk)
body = X.extrude(part, X.section(part, ls), (0, 0, 1), 0, 20).GetBodies()[0]
top = X.extreme_plane(body, (0, 0, 1), max)
X.edge_blend(part, list(top.GetEdges()), "fillet_r")
bottom = X.extreme_plane(body, (0, 0, -1), max)
X.chamfer(part, list(bottom.GetEdges()), "chamfer_d")
