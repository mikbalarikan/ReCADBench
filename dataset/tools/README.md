# dataset/tools — dataset tooling (NO part data in this public repo)

Runs on the **evaluator host** (GT authoring). Real scans are the input ([ADR 0002](../../docs/adr/0002-real-scan-input.md));
the authoring guide is `recadbench-gt/AUTHORING.md`. Install: `pip install -e ./spec -r dataset/requirements.txt`.

| Tool | What it does |
|---|---|
| `register_gt.py <gt>/parts/<id> [--init identity\|pca\|T.json] [--gt-mesh cad.stl] [--write]` | Registers the raw scan to `gt.step` (coarse PCA or given init, then trimmed point-to-plane ICP, fixed seed). Measures the **noise floor** (GT vs scan deviation) and **observability** (GT surface the scan covers; open-boundary and opposite-normal masks). With `--write`: updates `meta.yaml` (scan sha256/triangles, `registration`, `scan_floor`, `observability`) and writes `registration_report.json` + `observability_mask.npz`. |
| `make_task.py --gt-root <gt>/parts --data-root <data> [ids]` | Packages parts into `recadbench-data`: `input.stl` = **byte-identical copy of the scan** (sha256 checked), `task.yaml` (agent-visible scan metadata only, validated), `MANIFEST.json`, `splits/`. Synthetic parts: tessellates `gt.step`. Refuses to write inside this repo. |
| `export_hf.py --data-root <data> --gt-root <gt>/parts --out STAGING [--parts ids\|--split dev] [--previews DIR] [--push --inputs-repo R --gt-repo R]` | Stages the Hugging Face datasets (Open track): `STAGING/inputs/` (`parts/<id>/{input.stl, task.yaml, preview.png?}`, `SUBMISSION.md`, `tools/{sanity_check_submission.py, submission.py}` (runs standalone from a download), `tasks.json`, dataset card) and `STAGING/gt/` (`parts/<id>/{gt.step, gt_plan.json, key_dims.yaml, meta.yaml, observability_mask.npz, registration_report.json}`, PRIVATE card). Allowlist copy: never `gt_native*`, never `scan_raw.stl`. Refuses STAGING inside this repo, inside any other ReCADBench checkout or git work tree with a remote, or overlapping the data/GT roots. Default: no push. `--push` (owner only) creates the GT repo private and refuses to upload unless the Hub reports it private; the inputs repo is created private unless `--inputs-public`. |
| `check_public.py` | CI guard: no STL/STEP/PRT outside synthetic fixtures, no GT files anywhere. |
| `scanreg.py` | Library behind `register_gt.py` (loading, PCA coarse alignment, ICP, distances, masks). Approach ported from `agentic_STL-to-CAD/skills/stl-re-verify`. |

Tests (`dataset/tests/`) use only synthetic meshes generated in memory.
