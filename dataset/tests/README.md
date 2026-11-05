# dataset/tests

Tests for `dataset/tools` on synthetic meshes only (an asymmetric block GT; a "scan" with 0.03 mm noise, a hole and a known rigid transform). Covers ICP accuracy from near and from PCA starts, noise floor, observability, determinism, and the register → make_task pipeline (byte-identical scan copy, no GT leakage, sha256 checks, splits/manifest, public-repo refusal). Added in P6.
