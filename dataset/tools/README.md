# dataset/tools — dataset tooling (NO part data in this public repo)

Runs on the **evaluator host** (GT authoring). Real scans are the input ([ADR 0002](../../docs/adr/0002-real-scan-input.md));
the authoring guide is `recadbench-gt/AUTHORING.md`. Install: `pip install -e ./spec -r dataset/requirements.txt`.

| Tool | What it does |
|---|---|
| `register_gt.py <gt>/parts/<id> [--init identity\|pca\|T.json] [--gt-mesh cad.stl] [--write]` | Registers the raw scan to `gt.step` (coarse PCA or given init, then trimmed point-to-plane ICP, fixed seed). Measures the **noise floor** (GT vs scan deviation) and **observability** (GT surface the scan covers; open-boundary and opposite-normal masks). With `--write`: updates `meta.yaml` (scan sha256/triangles, `registration`, `scan_floor`, `observability`) and writes `registration_report.json` + `observability_mask.npz`. |
| `make_task.py --gt-root <gt>/parts --data-root <data> [ids]` | Packages parts into `recadbench-data`: `input.stl` = **byte-identical copy of the scan** (sha256 checked), `task.yaml` (agent-visible scan metadata only, validated), `MANIFEST.json`, `splits/`. Synthetic parts: tessellates `gt.step`. Refuses to write inside this repo. |
| `check_public.py` | CI guard: no STL/STEP/PRT outside synthetic fixtures, no GT files anywhere. |
| `scanreg.py` | Library behind `register_gt.py` (loading, PCA coarse alignment, ICP, distances, masks). Approach ported from `agentic_STL-to-CAD/skills/stl-re-verify`. |

Tests (`dataset/tests/`) use only synthetic meshes generated in memory.
