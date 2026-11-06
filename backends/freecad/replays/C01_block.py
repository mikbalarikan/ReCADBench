"""C01_block: rect 40x30 on D1 (XY), Pad `height` along +Z. Features: extrude (1)."""

from _fc import bind, finish, new_body, params, rect, sketch

doc, body = new_body("C01_block")
params(doc, {"height": 20.0})
sk = sketch(body, "XY_Plane")
rect(sk, 0, 0, 40, 30)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
bind(pad, "Length", "height")
finish(doc, body, pad)
