"""S1 geometric deviation vs gt.step (40). 03_SCORING §3 S1 with the ADR 0002 scan rules.

Two-way sampling GT B-rep <-> prediction B-rep (``n_samples`` each, fixed seeds), unsigned
distances to the other surface (exact, see ``geom.Surface``).

- **Observable region**: every GT sample is looked up in ``observability_mask.npz`` (nearest
  mask sample; the mask was sampled on the same gt.step). Only GT samples the scan does not
  cover are excluded (03_SCORING: "GT sample points the scan does not cover are excluded"):
  ``p_tight``/``p_wide`` use the observable GT->pred samples plus **every** pred->GT sample, so
  material added over an unobserved GT patch is always scored. The unobserved GT->pred samples
  are reported as ``p_*_inferred`` (not scored in v1). Synthetic parts: all observable.
- **Noise floor**: ``band_eff = max(class band, k * scan_floor.scan_to_gt_p95_mm)``.
- ``S1 = 24 * lin(p_tight; 60% -> 0, target -> 1) + 16 * lin(p_wide; 85% -> 0, target -> 1)``.
- Reported only: mean, RMS, P95, max (observable), Hausdorff (all). Never scored on max.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .config import EvalConfig, lin
from .geom import Samples, Surface

FAR_MM = 5.0  # distances beyond this are upper bounds (far outside every band)


@dataclass
class Bands:
    tight: float
    wide: float
    tight_target: float
    wide_target: float
    tight_class: float
    wide_class: float


def effective_bands(cls_bands: tuple[float, float, float, float], floor_p95: float,
                    k: float) -> Bands:
    tight, tt, wide, wt = cls_bands
    return Bands(max(tight, k * floor_p95), max(wide, k * floor_p95), tt, wt, tight, wide)


def observable_labels(points: np.ndarray, mask_pts: np.ndarray | None,
                      mask_obs: np.ndarray | None) -> np.ndarray:
    if mask_pts is None or mask_obs is None:
        return np.ones(len(points), dtype=bool)
    _, idx = cKDTree(mask_pts).query(points)
    return mask_obs[idx]


def _share(d: np.ndarray, band: float) -> float | None:
    return float(np.mean(d <= band + 1e-12)) if len(d) else None


def s1(gt: Samples, gt_obs: np.ndarray, gt_surface: Surface, pred: Samples,
       pred_surface: Surface, bands: Bands, cfg: EvalConfig) -> dict:
    d_g2p = pred_surface.query(gt.points, beyond_mm=FAR_MM)[0]
    d_p2g = gt_surface.query(pred.points, beyond_mm=FAR_MM)[0]
    d_obs = np.concatenate([d_g2p[gt_obs], d_p2g])
    d_inf = d_g2p[~gt_obs]
    d_all = np.concatenate([d_g2p, d_p2g])
    p_tight = _share(d_obs, bands.tight) or 0.0
    p_wide = _share(d_obs, bands.wide) or 0.0
    score = 24.0 * lin(p_tight, cfg.s1_tight_floor, bands.tight_target) + 16.0 * lin(
        p_wide, cfg.s1_wide_floor, bands.wide_target
    )
    out = {
        "S1": score,
        "p_tight": p_tight,
        "p_wide": p_wide,
        "p_tight_inferred": _share(d_inf, bands.tight),
        "p_wide_inferred": _share(d_inf, bands.wide),
        "mean": float(d_obs.mean()) if len(d_obs) else 0.0,
        "rms": float(np.sqrt(np.mean(d_obs**2))) if len(d_obs) else 0.0,
        "p95": float(np.quantile(d_obs, 0.95)) if len(d_obs) else 0.0,
        "max": float(d_obs.max()) if len(d_obs) else 0.0,
        "hausdorff": float(d_all.max()),
        "targets_met": bool(p_tight >= bands.tight_target and p_wide >= bands.wide_target),
        "detail": {
            "gt_to_pred_p_tight": _share(d_g2p[gt_obs], bands.tight),
            "pred_to_gt_p_tight": _share(d_p2g, bands.tight),
            "observable_share_gt_samples": float(gt_obs.mean()),
            "n_observable": int(len(d_obs)),
            "n_inferred": int(len(d_inf)),
        },
    }
    return out
