"""C10_draft_pocket: block 50x40x20; pocket rect 30x20 sketched on P_top (z = 20), Extrude
Subtract `pocket_depth` (10) along -Z; Draft `draft_deg` (5) on the 4 pocket walls, stationary =
top face, pull +Z (the pocket narrows downward). Features: extrude, extrude, draft = 3."""

import _nxr as X

part = X.work()
d = X.Datums(part)
IR = X.ir()  # spec 0.3.0 IR of this case (parameters, pattern params)
X.ir_parameters(
    part, IR, unitless={"draft_deg"}
)  # a draft angle links only to a unitless expression
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
ang = X._ir.feature(IR, "F3")["params"]["angle_deg"]  # {"ref": "draft_deg"}
X.draft(part, walls, (0, 0, 1), X._ir.value(IR, ang), [top], link=ang["ref"])
