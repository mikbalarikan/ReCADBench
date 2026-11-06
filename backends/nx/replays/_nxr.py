"""Minimal NXOpen builder helpers for the conformance replays (NX 2206, Python 3.10).

Self-contained on purpose: the replays are part of the measurement suite and must not depend on
the L2 skillset (ReCADBench-cad-skills nx/lib). The recipes are the verified ones of the owner's
NX helper modules (builder lifecycle create -> configure -> commit -> Destroy in finally; floats
only; named expressions drive the design dimensions).

A replay is an ordinary journal for `recadbench nx-run`: it models in the work part the wrapper
opened (``NXOpen.Session.GetSession().Parts.Work``).
"""

import math

import _ir
import NXOpen
import NXOpen.Features
import NXOpen.GeometricUtilities as GU

S = NXOpen.Session.GetSession()
UPD = NXOpen.SmartObject.UpdateOption.WithinModeling
BOOL = GU.BooleanOperation.BooleanType
NF = NXOpen.Features.Feature.Null
NULL = NXOpen.NXObject.Null
SK = NXOpen.Sketch
A = SK.AssocType
CP = SK.ConstraintPointType
PAT = GU.PatternDefinition.PatternEnum
SPACE = GU.PatternSpacing.SpacingType
PLANE = 22  # UF face type


def P(x, y, z):
    return NXOpen.Point3d(float(x), float(y), float(z))


def V(x, y, z):
    return NXOpen.Vector3d(float(x), float(y), float(z))


def work():
    return S.Parts.Work


def units(part):
    uc = part.UnitCollection
    return {"mm": uc.FindObject("MilliMeter"), "deg": uc.FindObject("Degrees"), None: None}


def expr(part, name, value, unit="mm"):
    return part.Expressions.CreateWithUnits(f"{name}={float(value)!r}", units(part)[unit])


def ir():
    """The spec 0.3.0 IR of the running replay (ir_cases/<journal stem>/re_plan.v1.json)."""
    return _ir.for_journal()


def ir_parameters(part, doc, unitless=()):
    """One named expression per IR parameter (IR `parameters[].name` = NX expression name, the
    set-param handle); names in `unitless` get no unit (e.g. a draft angle)."""
    for name, v in _ir.parameters(doc).items():
        expr(part, name, v, None if name in unitless else "mm")


def update():
    S.UpdateManager.SetDefaultUpdateFailureAction(NXOpen.Update.FailureOption.AcceptAll)
    S.UpdateManager.DoUpdate(S.SetUndoMark(NXOpen.Session.MarkVisibility.Invisible, "upd"))


# ---------------------------------------------------------------- datums and smart objects
class Datums:
    """Principal planes / axes / origin of the template's Datum Coordinate System."""

    def __init__(self, part):
        csys = [f for f in part.Features.GetFeatures() if f.FeatureType == "DATUM_CSYS"][0]
        pre, f = csys.JournalIdentifier, part.Datums.FindObject
        self.xy, self.yz, self.xz = f(pre + " XY plane"), f(pre + " YZ plane"), f(pre + " XZ plane")
        self.x, self.y, self.z = f(pre + " X axis"), f(pre + " Y axis"), f(pre + " Z axis")
        self.origin = csys.FindObject("POINT 1")


def point(part, x, y, z):
    """Associative point; coordinates may be numbers or expression formulas."""
    mm = units(part)["mm"]
    sc = []
    for v in (x, y, z):
        e = part.Expressions.CreateSystemExpressionWithUnits(str(v), mm)
        sc.append(
            part.Scalars.CreateScalarExpression(e, NXOpen.Scalar.DimensionalityType.NotSet, UPD)
        )
    return part.Points.CreatePoint(sc[0], sc[1], sc[2], UPD)


def direction(part, vec):
    return part.Directions.CreateDirection(P(0, 0, 0), V(*vec), UPD)


def axis(part, origin, vec):
    return part.Axes.CreateAxis(point(part, *origin), direction(part, vec), UPD)


def datum_plane(part, origin, normal):
    b = part.Features.CreateDatumPlaneBuilder(NF)
    try:
        pl = b.GetPlane()
        pl.SetMethod(NXOpen.PlaneTypes.MethodType.PointDir)
        pl.SetGeometry([point(part, *origin), direction(part, normal)])
        pl.Evaluate()
        f = b.CommitFeature()
    finally:
        b.Destroy()
    return f.DatumPlane


# ---------------------------------------------------------------- sketches
def new_sketch(part, plane, haxis, origin):
    """Active sketch on a datum plane; Continuous Auto Dimensioning off (a batch session starts
    with it on, which would close every open DOF with an automatic dimension)."""
    S.Preferences.Sketch.ContinuousAutoDimensioning = False
    sb = part.Sketches.CreateSimpleSketchInPlaceBuilder()
    try:
        sb.UseWorkPartOrigin = False
        d = part.Directions.CreateDirection(haxis, NXOpen.Sense.Forward, UPD)
        xf = part.Xforms.CreateXformByPlaneXDirPoint(plane, d, origin, UPD, 0.625, False, False)
        sb.CoordinateSystem = part.CoordinateSystems.CreateCoordinateSystem(xf, UPD)
        sb.HorizontalReference.Value = haxis
        sk = sb.Commit()
    finally:
        sb.Destroy()
    sk.Activate(SK.ViewReorient.FalseValue)
    return sk


def to3d(sk):
    o, m = sk.Origin, sk.Orientation.Element
    return lambda u, v: P(
        o.X + u * m.Xx + v * m.Yx, o.Y + u * m.Xy + v * m.Yy, o.Z + u * m.Xz + v * m.Yz
    )


def cg(geom, pt=None):
    g = SK.ConstraintGeometry()
    g.Geometry = geom
    g.PointType = pt if pt is not None else CP.NotSet
    g.SplineDefiningPointIndex = 0
    return g


def dg(geom, assoc=None, hp=None):
    d = SK.DimensionGeometry()
    d.Geometry = geom
    d.AssocType = assoc if assoc is not None else A.NotSet
    d.AssocValue = 0
    d.HelpPoint = hp if hp is not None else P(0, 0, 0)
    d.View = NULL
    return d


def help_at(p):
    h = SK.ConstraintGeometryHelp()
    h.Type = SK.ConstraintGeometryHelpType.Point
    h.Point = p
    h.Parameter = 0.0
    return h


def hv_polygon(part, sk, pts):
    """Closed polygon of H/V lines (sketch coords) with coincident corners + H/V constraints."""
    t = to3d(sk)
    n = len(pts)
    ls = [part.Curves.CreateLine(t(*pts[i]), t(*pts[(i + 1) % n])) for i in range(n)]
    for ln in ls:
        sk.AddGeometry(ln, SK.InferConstraintsOption.InferCoincidentConstraints)
    for i, ln in enumerate(ls):
        if abs(pts[i][1] - pts[(i + 1) % n][1]) < 1e-9:
            sk.CreateHorizontalConstraint(cg(ln))
        else:
            sk.CreateVerticalConstraint(cg(ln))
    return ls


def pdim(sk, ref, ref_help, geom, assoc, text_at, formula):
    """Perpendicular distance from a line-like reference to a point of geom = formula."""
    c = sk.CreateDimension(
        SK.ConstraintType.PerpendicularDim,
        dg(ref, None, ref_help),
        dg(geom, assoc),
        text_at,
        NXOpen.Expression.Null,
    )
    c.AssociatedExpression.RightHandSide = str(formula)
    return c


def on_curve(sk, line, vertex, curve, at):
    sk.CreatePointOnCurveConstraint(cg(line, vertex), cg(curve), help_at(at))


def rect_centered(part, sk, centre, width, height, u0=0.0, v0=0.0):
    """Fully constrained rectangle centred on point `centre` (legacy-solver sketch of a batch part):
    four H/V lines, four perpendicular dimensions line -> centre of width/2, height/2."""
    t = to3d(sk)
    w, h = float(width_value(part, width)) / 2, float(width_value(part, height)) / 2
    u, v = u0, v0
    ls = hv_polygon(part, sk, [(u - w, v - h), (u + w, v - h), (u + w, v + h), (u - w, v + h)])
    for ln, mid, f in (
        (ls[1], (u + w, v), width),
        (ls[3], (u - w, v), width),
        (ls[0], (u, v - h), height),
        (ls[2], (u, v + h), height),
    ):
        pdim(sk, ln, t(*mid), centre, None, t((u + mid[0]) / 2, (v + mid[1]) / 2), f"({f})/2")
    return ls


def width_value(part, f):
    try:
        return float(f)
    except (TypeError, ValueError):
        return part.Expressions.FindObject(str(f)).Value


def circle(part, sk, centre, diameter, u0=0.0, v0=0.0):
    """Circle with its centre coincident with point `centre` and a diameter dimension."""
    t, m = to3d(sk), sk.Orientation.Element
    r = width_value(part, diameter) / 2.0
    arc = part.Curves.CreateArc(
        t(u0, v0), V(m.Xx, m.Xy, m.Xz), V(m.Yx, m.Yy, m.Yz), r, 0.0, 2 * math.pi
    )
    sk.AddGeometry(arc, SK.InferConstraintsOption.InferNoConstraints)
    sk.CreateCoincidentConstraint(cg(arc, CP.ArcCenter), cg(centre))
    c = sk.CreateDiameterDimension(
        dg(arc, None, t(u0 + r, v0)), t(u0 + 2 * r, v0 + 2 * r), NXOpen.Expression.Null
    )
    c.AssociatedExpression.RightHandSide = str(diameter)
    return [arc]


def finish(sk):
    sk.Update()
    st, dof = sk.GetStatus()
    sk.Deactivate(SK.ViewReorient.FalseValue, SK.UpdateLevel.Model)
    return dof


def section(part, curves):
    sec = part.Sections.CreateSection(0.0095, 0.01, 0.5)
    sec.SetAllowedEntityTypes(NXOpen.Section.AllowTypes.OnlyCurves)
    sec.AllowSelfIntersection(False)
    rule = part.ScRuleFactory.CreateRuleCurveDumb(list(curves))
    sec.AddToSection(
        [rule], list(curves)[0], NULL, NULL, P(0, 0, 0), NXOpen.Section.Mode.Create, False
    )
    return sec


def collector(part, faces=None, edges=None, bodies=None):
    sc = part.ScCollectors.CreateCollector()
    rules = []
    if faces:
        rules.append(part.ScRuleFactory.CreateRuleFaceDumb(list(faces)))
    if edges:
        rules.append(part.ScRuleFactory.CreateRuleEdgeDumb(list(edges)))
    if bodies:
        rules.append(part.ScRuleFactory.CreateRuleBodyDumb(list(bodies), True))
    sc.ReplaceRules(rules, False)
    return sc


# ---------------------------------------------------------------- features
def _bool(b, op, target):
    op = BOOL.Create if op is None else op
    b.BooleanOperation.Type = op
    if op != BOOL.Create:
        b.BooleanOperation.SetTargetBodies([target])


def extrude(part, sec, vec, start, end, op=None, target=None):
    b = part.Features.CreateExtrudeBuilder(NF)
    try:
        b.Section = sec
        b.Direction = direction(part, vec)
        b.Limits.StartExtend.Value.RightHandSide = str(start)
        b.Limits.EndExtend.Value.RightHandSide = str(end)
        _bool(b, op, target)
        return b.CommitFeature()
    finally:
        b.Destroy()


def revolve(part, sec, ax, start=0, end=360, op=None, target=None):
    b = part.Features.CreateRevolveBuilder(NF)
    try:
        b.Tolerance = 0.01
        b.Section = sec
        b.Axis = ax
        b.Limits.StartExtend.Value.RightHandSide = str(start)
        b.Limits.EndExtend.Value.RightHandSide = str(end)
        _bool(b, op, target)
        return b.CommitFeature()
    finally:
        b.Destroy()


def hole(part, body, points, diameter, vector=(0, 0, -1), cbore=None):
    """Through General Hole (HolePackage) at associative points; cbore=(diameter, depth)."""
    HPB = NXOpen.Features.HolePackageBuilder
    pts = [point(part, *p) for p in points]
    h = part.Features.CreateHolePackageBuilder(NXOpen.Features.HolePackage.Null)
    try:
        h.Type = HPB.Types.GeneralHole
        if cbore:
            h.GeneralHoleForm = HPB.HoleForms.Counterbored
            h.GeneralCounterboreDiameter.RightHandSide = str(cbore[0])
            h.GeneralCounterboreDepth.RightHandSide = str(cbore[1])
            h.GeneralCounterboreHoleDiameter.RightHandSide = str(diameter)
        else:
            h.GeneralHoleForm = HPB.HoleForms.Simple
            h.GeneralSimpleHoleDiameter.RightHandSide = str(diameter)
            h.GeneralTipAngle.RightHandSide = "0"
        h.HoleDepthLimitOption = HPB.HoleDepthLimitOptions.ThroughBody
        h.Tolerance = 0.01
        h.StartExtensionEnabled = False
        h.BooleanOperation.Type = BOOL.Subtract
        h.BooleanOperation.SetTargetBodies([body])
        h.HolePosition.AddToSection(
            [part.ScRuleFactory.CreateRuleCurveDumbFromPoints(pts)],
            NULL,
            NULL,
            NULL,
            P(0, 0, 0),
            NXOpen.Section.Mode.Create,
            False,
        )
        h.ProjectionDirection.ProjectDirectionMethod = GU.ProjectionOptions.DirectionType.Vector
        h.ProjectionDirection.ProjectVector = direction(part, vector)
        return h.CommitFeature()
    finally:
        h.Destroy()


def _pattern(part, features, layout):
    b = part.Features.CreatePatternFeatureBuilder(NF)
    try:
        layout(b.PatternService)
        b.PatternMethod = NXOpen.Features.PatternFeatureBuilder.PatternMethodOptions.Simple
        b.FeatureList.Add(list(features))
        return b.CommitFeature()
    finally:
        b.Destroy()


def pattern_linear(part, features, xdir, nx, px, ydir=None, ny=None, py=None):
    def layout(ps):
        ps.PatternType = PAT.Linear
        rd = ps.RectangularDefinition
        rd.XDirection = direction(part, xdir)
        rd.XSpacing.SpaceType = SPACE.Offset
        rd.XSpacing.NCopies.RightHandSide = str(nx)
        rd.XSpacing.PitchDistance.RightHandSide = str(px)
        if ydir is not None:
            rd.UseYDirectionToggle = True
            rd.YDirection = direction(part, ydir)
            rd.YSpacing.SpaceType = SPACE.Offset
            rd.YSpacing.NCopies.RightHandSide = str(ny)
            rd.YSpacing.PitchDistance.RightHandSide = str(py)

    return _pattern(part, features, layout)


def pattern_circular(part, features, origin, vec, count, span=360, pitch=None):
    """Circular Pattern Feature. Full circle: Span `span` (360). With `pitch` (formula of the
    angle between neighbours, IR 0.3.0 step): Offset spacing, instance k at k * pitch."""

    def layout(ps):
        ps.PatternType = PAT.Circular
        cd = ps.CircularDefinition
        cd.RotationAxis = axis(part, origin, vec)
        cd.AngularSpacing.NCopies.RightHandSide = str(count)
        if pitch is None:
            cd.AngularSpacing.SpaceType = SPACE.Span
            cd.AngularSpacing.SpanAngle.RightHandSide = str(span)
        else:
            cd.AngularSpacing.SpaceType = SPACE.Offset
            cd.AngularSpacing.PitchAngle.RightHandSide = str(pitch)

    return _pattern(part, features, layout)


def ir_pattern_circular(part, doc, fid, features, origin, vec):
    """pattern_circular of IR feature `fid` (spec 0.3.0: angle_deg = total sweep or pitch_deg)."""
    mode, angle, count = _ir.circular_spacing(doc, _ir.feature(doc, fid)["params"])
    if mode == "span":
        return pattern_circular(part, features, origin, vec, count, span=angle)
    return pattern_circular(part, features, origin, vec, count, pitch=angle)


def mirror_feature(part, features, plane):
    b = part.Features.CreateMirrorBuilder(NXOpen.Features.Mirror.Null)
    try:
        b.PatternService.PatternType = PAT.Mirror
        md = b.PatternService.MirrorDefinition
        md.PlaneOption = GU.MirrorPattern.PlaneOptions.Existing
        md.ExistingPlane.Value = plane
        b.FeatureList.Add(list(features))
        return b.CommitFeature()
    finally:
        b.Destroy()


def unite(part, target, tools):
    b = part.Features.CreateBooleanBuilderUsingCollector(NXOpen.Features.BooleanFeature.Null)
    try:
        b.Tolerance = 0.01
        b.Operation = NXOpen.Features.Feature.BooleanType.Unite
        b.Targets.Add(target)
        b.ToolBodyCollector = collector(part, bodies=tools)
        return b.CommitFeature()
    finally:
        b.Destroy()


def edge_blend(part, edges, radius):
    b = part.Features.CreateEdgeBlendBuilder(NF)
    try:
        b.Tolerance = 0.01
        b.AddChainset(collector(part, edges=edges), str(radius))
        return b.CommitFeature()
    finally:
        b.Destroy()


def chamfer(part, edges, distance):
    CB = NXOpen.Features.ChamferBuilder
    b = part.Features.CreateChamferBuilder(NF)
    try:
        b.Option = CB.ChamferOption.SymmetricOffsets
        b.Method = CB.OffsetMethod.EdgesAlongFaces
        b.FirstOffset = str(distance)
        b.Tolerance = 0.01
        b.SmartCollector = collector(part, edges=edges)
        return b.CommitFeature()
    finally:
        b.Destroy()


def shell(part, body, removed_faces, thickness):
    """Inward shell (outer size kept), removed faces become openings."""
    b = part.Features.CreateShellBuilder(NF)
    try:
        b.Tolerance = 0.01
        b.Body = body
        b.RemovedFacesCollector = collector(part, faces=removed_faces)
        b.DefaultThickness.RightHandSide = str(thickness)
        b.DefaultThicknessFlip = True
        return b.CommitFeature()
    finally:
        b.Destroy()


def draft(part, faces, pull, angle, stationary_faces, link=None):
    """Face draft from a stationary face. The angle must be a NUMBER at creation; `link` (the name
    of a UNITLESS expression) drives it afterwards. Positive angle shrinks the section along +pull
    from the stationary face."""
    DB = NXOpen.Features.DraftBuilder
    b = part.Features.CreateDraftBuilder(NF)
    try:
        b.TypeOfDraft = DB.Type.Face
        b.AngleTolerance = 0.1
        b.DistanceTolerance = 0.01
        b.Direction = direction(part, pull)
        b.DraftReferencesMethod = DB.DraftReferencesMethods.StationaryFace
        b.StationaryReference.ReplaceRules(
            [part.ScRuleFactory.CreateRuleFaceDumb(list(stationary_faces))], False
        )
        ecs = part.CreateExpressionCollectorSet(
            collector(part, faces=list(faces)), str(float(angle)), "Degrees", 0
        )
        b.FaceSetAngleExpressionList.Append(ecs)
        f = b.CommitFeature()
    finally:
        b.Destroy()
    if link:
        f.GetExpressions()[0].RightHandSide = str(link)
        update()
    return f


# ---------------------------------------------------------------- face / edge queries
def _ufs():
    import NXOpen.UF

    return NXOpen.UF.UFSession.GetUFSession()


def face_data(face):
    t, pt, d, _box, rad, _rd, _nd = _ufs().Modeling.AskFaceData(face.Tag)
    return t, list(pt), list(d), rad


def bbox(obj):
    return list(_ufs().ModlGeneral.AskBoundingBox(obj.Tag))


def planes(body, normal, tol=1e-6):
    return [
        f
        for f in body.GetFaces()
        if face_data(f)[0] == PLANE
        and all(abs(a - b) < tol for a, b in zip(face_data(f)[2], normal, strict=False))
    ]


def one(objs, what):
    objs = list(objs)
    if len(objs) != 1:
        raise RuntimeError(f"{what}: expected exactly 1 match, got {len(objs)}")
    return objs[0]


def extreme_plane(body, normal, pick=max):
    """The planar face with outward normal `normal` at the max / min coordinate along it."""
    i = [abs(c) for c in normal].index(1.0)
    fs = planes(body, normal)
    key = lambda f: bbox(f)[i]  # noqa: E731
    best = pick(fs, key=key)
    return one([f for f in fs if abs(key(f) - key(best)) < 1e-6], f"extreme plane {normal}")
