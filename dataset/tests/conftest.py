import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


def _boxes() -> trimesh.Trimesh:
    """Asymmetric synthetic 'GT': a plate plus a separate offset block (no symmetry for PCA)."""
    a = trimesh.creation.box(extents=(40, 30, 10))
    b = trimesh.creation.box(extents=(10, 8, 14))
    b.apply_translation((28, 14, 2))
    m = trimesh.util.concatenate([a, b])
    v, f = trimesh.remesh.subdivide_to_size(m.vertices, m.faces, max_edge=1.5)
    return trimesh.Trimesh(v, f, process=False)


def _rigid(deg: float, axis, t) -> np.ndarray:
    T = trimesh.transformations.rotation_matrix(np.radians(deg), axis)
    T[:3, 3] = t
    return T


@pytest.fixture(scope="session")
def gt_mesh() -> trimesh.Trimesh:
    return _boxes()


@pytest.fixture(scope="session")
def scan_case(gt_mesh):
    """A fake scan: GT + 0.03 mm noise, a hole in the top face, moved by a known rigid transform.

    Returns (scan_mesh_in_scanner_frame, T_scan_to_gt_true, hole_area_fraction)."""
    rng = np.random.default_rng(7)
    m = gt_mesh.copy()
    c = m.triangles_center
    hole = (c[:, 2] > 4.9) & (np.hypot(c[:, 0] + 8, c[:, 1] + 5) < 6)
    hole_frac = float(m.area_faces[hole].sum() / m.area)
    m.update_faces(~hole)
    m.remove_unreferenced_vertices()
    m.vertices = m.vertices + rng.normal(0, 0.03, m.vertices.shape)
    T_gt_to_scan = _rigid(33.0, (1, 2, 3), (120.0, -40.0, 15.0))
    m.apply_transform(T_gt_to_scan)
    return m, np.linalg.inv(T_gt_to_scan), hole_frac
