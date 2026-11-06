import numpy as np
import scanreg
import trimesh


def _err(T_est, T_true, pts=None):
    """(rotation error in degrees, max displacement in mm of the part's own points)."""
    deg, _ = scanreg.rotation_delta(np.linalg.inv(T_true) @ T_est)
    if pts is None:
        return deg, 0.0
    q = scanreg.apply(np.linalg.inv(T_true), pts)  # GT points expressed in the scan frame
    return deg, float(np.linalg.norm(scanreg.apply(T_est, q) - pts, axis=1).max())


def test_kabsch_recovers_exact_transform(gt_mesh) -> None:
    T = np.eye(4)
    T[:3, :3] = np.linalg.qr(np.random.default_rng(1).normal(size=(3, 3)))[0]
    if np.linalg.det(T[:3, :3]) < 0:
        T[:3, 0] *= -1
    T[:3, 3] = (5, -2, 9)
    src = gt_mesh.vertices[:500]
    deg, mm = _err(scanreg.kabsch(src, scanreg.apply(T, src)), T, src)
    assert deg < 1e-4 and mm < 1e-9


def test_icp_from_near_start(scan_case, gt_mesh) -> None:
    scan, T_true, _ = scan_case
    T0 = T_true.copy()
    T0[:3, 3] += (0.8, -0.5, 0.4)
    res = scanreg.icp(scan, gt_mesh, T0, seed=3, n=4000)
    deg, mm = _err(res.T, T_true, gt_mesh.vertices)
    assert res.converged
    assert deg < 0.05 and mm < 0.02, (deg, mm)


def test_coarse_pca_then_icp_from_scanner_frame(scan_case, gt_mesh) -> None:
    scan, T_true, _ = scan_case
    T0 = scanreg.coarse_icp(scan, gt_mesh, scanreg.coarse_pca(scan, gt_mesh, seed=5), seed=5)
    res = scanreg.icp(scan, gt_mesh, T0, seed=3, n=4000)
    deg, mm = _err(res.T, T_true, gt_mesh.vertices)
    assert res.converged
    assert deg < 0.05 and mm < 0.02, (deg, mm)


def test_floor_matches_noise_and_hole_is_unobservable(scan_case, gt_mesh) -> None:
    scan, T_true, hole_frac = scan_case
    fl = scanreg.floor_and_observability(
        scan, gt_mesh, T_true, threshold_mm=0.5, seed=0, n_scan=20000, n_gt=20000
    )
    # vertex noise σ=0.03 mm → normal-direction distance p95 ≈ 0.06
    assert 0.03 < fl.scan_to_gt["p95"] < 0.10, fl.scan_to_gt
    assert fl.gt_to_scan_observable["p95"] < 0.10
    assert abs((1 - fl.observable_fraction) - hole_frac) < 0.01, (fl.observable_fraction, hole_frac)
    assert fl.observable.shape == (20000,)


def test_deterministic(scan_case, gt_mesh) -> None:
    scan, T_true, _ = scan_case
    a = scanreg.icp(scan, gt_mesh, T_true, seed=9, n=2000, max_iter=5)
    b = scanreg.icp(scan, gt_mesh, T_true, seed=9, n=2000, max_iter=5)
    assert np.array_equal(a.T, b.T)


def test_boundary_faces(gt_mesh, scan_case) -> None:
    assert not scanreg.boundary_faces(gt_mesh).any()  # closed boxes
    assert scanreg.boundary_faces(scan_case[0]).any()  # the hole rim


def test_scanner_table_is_extraneous_not_floor(scan_case, gt_mesh) -> None:
    """A scan that still contains the scanner table (R385 SL01/SL03/SL04): ICP must still lock
    on, the floor must ignore the table, and the table share must be reported."""
    scan, T_true, _ = scan_case
    table = trimesh.creation.box(extents=(90, 70, 1.0))
    table.apply_translation((0, 0, -5.5 - 0.5 - 2.0))  # 2 mm gap below the part's bottom face
    table.apply_transform(np.linalg.inv(T_true))  # into the scanner frame, like the scan
    with_table = trimesh.util.concatenate([scan, table])
    T0 = T_true.copy()
    T0[:3, 3] += (0.6, 0.4, -0.3)
    res = scanreg.icp(with_table, gt_mesh, T0, seed=3, n=6000)
    deg, mm = _err(res.T, T_true, gt_mesh.vertices)
    assert res.converged and deg < 0.05 and mm < 0.03, (deg, mm)
    fl = scanreg.floor_and_observability(
        with_table, gt_mesh, res.T, seed=0, n_scan=20000, n_gt=20000, extraneous_mm=1.5
    )
    assert fl.extraneous_fraction > 0.3  # the table is a large share of the scan area
    assert fl.scan_to_gt["p95"] < 0.10, fl.scan_to_gt  # floor unaffected by the table
    assert fl.scan_to_gt_all["p95"] > 1.0  # it would have been without the mask
