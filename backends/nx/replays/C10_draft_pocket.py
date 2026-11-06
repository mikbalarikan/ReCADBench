"""C10_draft_pocket: block 50x40x20; pocket rect 30x20 sketched on P_top (z = 20), Extrude
Subtract `pocket_depth` (10) along -Z; Draft `draft_deg` (5) on the 4 pocket walls, stationary =
top face, pull +Z (the pocket narrows downward). Features: extrude, extrude, draft = 3."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "pocket_depth", 10.0)
X.expr(part, "draft_deg", 5.0, None)  # unitless: a draft angle can only link to a unitless one
sk = X.new_sketch(part, d.xy, d.x, d.origin)
ls = X.rect_centered(part, sk, d.origin, 50, 40)
X.finish(sk)
body = X.extrude(part, X.section(part, ls), (0, 0, 1), 0, 20).GetBodies()[0]
p_top = X.datum_plane(part, (0, 0, 20), (0, 0, 1))
sk2 = X.new_sketch(part, p_top, d.x, d.origin)
ls2 = X.rect_centered(part, sk2, X.point(part, 0, 0, 20), 30, 20)
X.finish(sk2)
pocket = X.extrude(
    part, X.section(part, ls2), (0, 0, -1), 0, "pocket_depth", op=X.BOOL.Subtract, target=body
)
walls = [
    f
    for f in pocket.GetFaces()
    if X.face_data(f)[0] == X.PLANE and abs(X.face_data(f)[2][2]) < 1e-9
]
if len(walls) != 4:
    raise RuntimeError(f"pocket walls: {len(walls)}")
top = X.extreme_plane(body, (0, 0, 1), max)
X.draft(part, walls, (0, 0, 1), 5.0, [top], link="draft_deg")
