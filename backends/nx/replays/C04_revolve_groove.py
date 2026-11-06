"""C04_revolve_groove: rect R1 (x 0..10, z 0..`shaft_len`) on P_xz revolved 360 deg about Z;
groove rect R2 (x 8..12, z 28..32) revolved 360 deg as a Subtract. Features: revolve, revolve = 2.

Both sketches are fully constrained; `shaft_len` is the dimension of the top edge of R1 from the X
axis (IR convention 8: sketch-only parameter)."""

import _nxr as X

part = X.work()
d = X.Datums(part)
X.expr(part, "shaft_len", 60.0)
p_xz = X.datum_plane(part, (0, 0, 0), (0, -1, 0))  # sketch u = +X, v = +Z
P = X.P


def rect(pts):
    sk = X.new_sketch(part, p_xz, d.x, d.origin)
    return sk, X.hv_polygon(part, sk, pts)


sk1, r1 = rect([(0, 0), (10, 0), (10, 60), (0, 60)])
X.on_curve(sk1, r1[0], X.CP.StartVertex, d.x, P(0, 0, 0))
X.on_curve(sk1, r1[0], X.CP.StartVertex, d.z, P(0, 0, 0))
X.pdim(sk1, d.z, P(0, 0, 5), r1[1], X.A.StartPoint, P(5, 0, -5), "10")
X.pdim(sk1, d.x, P(5, 0, 0), r1[2], X.A.StartPoint, P(15, 0, 30), "shaft_len")
X.finish(sk1)
shaft = X.revolve(part, X.section(part, r1), X.axis(part, (0, 0, 0), (0, 0, 1))).GetBodies()[0]

sk2, r2 = rect([(8, 28), (12, 28), (12, 32), (8, 32)])
X.pdim(sk2, d.z, P(0, 0, 28), r2[0], X.A.StartPoint, P(4, 0, 26), "8")
X.pdim(sk2, d.x, P(8, 0, 0), r2[0], X.A.StartPoint, P(6, 0, 14), "28")
X.pdim(sk2, d.z, P(0, 0, 32), r2[2], X.A.StartPoint, P(6, 0, 34), "12")
X.pdim(sk2, d.x, P(12, 0, 0), r2[2], X.A.StartPoint, P(14, 0, 16), "32")
X.finish(sk2)
X.revolve(
    part, X.section(part, r2), X.axis(part, (0, 0, 0), (0, 0, 1)), op=X.BOOL.Subtract, target=shaft
)
