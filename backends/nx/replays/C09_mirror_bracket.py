"""C09_mirror_bracket: half L-profile on P_xz (y = 10, normal -Y) extruded `bracket_depth` (20)
along -Y; through Hole d=`hole_d` (6) at (17, 0) on the base top (z = 6); Mirror Feature of
[extrude, hole] about the YZ plane. In NX 2206 the mirrored copy of a body-creating extrude is a
second body, so it is united into the first: that Unite merges ONE feature (the mirror) and is part
of it (taxonomy note), so the count stays extrude, hole, mirror = 3."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "bracket_depth", 20.0)
X.expr(part, "hole_d", 6.0)
p_xz = X.datum_plane(part, (0, 10, 0), (0, -1, 0))  # sketch u = +X, v = +Z
sk = X.new_sketch(part, p_xz, d.x, d.origin)
ls = X.hv_polygon(part, sk, [(0, 0), (35, 0), (35, 30), (29, 30), (29, 6), (0, 6)])
X.finish(sk)
ex = X.extrude(part, X.section(part, ls), (0, -1, 0), 0, "bracket_depth")
body = ex.GetBodies()[0]
h = X.hole(part, body, [(17, 0, 6)], "hole_d")
before = {b.Tag for b in part.Bodies}
X.mirror_feature(part, [ex, h], d.yz)
copies = [b for b in part.Bodies if b.Tag not in before and b.IsSolidBody]
if copies:
    X.unite(part, body, copies)
