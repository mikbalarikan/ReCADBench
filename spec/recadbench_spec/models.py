"""Pydantic v2 models for every ReCADBench contract.

The JSON Schemas in ``schemas/`` are the source of truth for structure; these models mirror them
for typed access in the other repos (tests check that field names and required sets match).
`recadbench_spec.validate` always runs the JSON Schema first, then parses into these models, then
applies the semantic rules that JSON Schema cannot express.

Conventions:
* Objects whose schema sets ``additionalProperties: false`` forbid extra keys; all others keep
  unknown keys (``extra="allow"``) so documents round-trip unchanged.
* Dump with ``model_dump(mode="json", by_alias=True, exclude_unset=True)`` to get the original
  document back (`to_document`).
* ``FeatureType`` is generated from ``feature_taxonomy.yaml`` at import time.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .taxonomy import feature_type_enum

SPEC_VERSION = "0.2.0"

FeatureType = feature_type_enum()

Vec2 = Annotated[list[float], Field(min_length=2, max_length=2)]
Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
SELECTOR_PATTERN = (
    r"^[A-Za-z][A-Za-z0-9_]*(:(plane|cylinder|cone|sphere|torus|bspline|any):([+-][XYZ]|axis|any)"
    r":(max|min|any)(:[0-9]+)?|:edges:(all|outer|inner|convex|concave))?$"
)
Selector = Annotated[str, StringConstraints(pattern=SELECTOR_PATTERN)]

BackendId = Literal["nx", "build123d", "solidcraft", "freecad"]
ToleranceClass = Literal["A", "B", "C"]
Tier = Literal[1, 2, 3]
Split = Literal["dev", "test"]
Track = Literal["T", "S", "E"]


class _Open(BaseModel):
    """Schema object without ``additionalProperties: false``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _Closed(BaseModel):
    """Schema object with ``additionalProperties: false``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _Document(_Open):
    def to_document(self) -> dict[str, Any]:
        """Dump back to a plain JSON-compatible dict (inverse of ``model_validate``)."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)


class _ClosedDocument(_Closed):
    to_document = _Document.to_document


# ------------------------------------------------------------------------------------ task
class TaskBudgets(_Open):
    max_wall_s: int
    max_turns: int
    max_tokens: int | None = None


class TaskScan(_Open):
    scanner: str
    mode: str | None = None
    sessions: Annotated[int, Field(ge=1)] | None = None
    triangles: Annotated[int, Field(ge=1)] | None = None
    watertight: bool | None = None
    frame: Literal["scanner", "prealigned"]
    known_defects: list[str] | None = None


class AuxInput(_Open):
    kind: Literal["calipers", "photos", "notes"]
    path: str


InputKind = Literal["scan", "synthetic_clean", "synthetic_degraded"]


class Task(_Document):
    """`task.yaml` — agent-visible task card."""

    spec_version: Literal["0.2.0"]
    part_id: str
    input_stl: str
    input_kind: InputKind
    scan: TaskScan | None = None
    aux_inputs: list[AuxInput] | None = None
    units: Literal["mm"]
    tolerance_class: ToleranceClass
    tier: Tier
    feature_budget_hint: int | None = None
    budgets: TaskBudgets
    deliverables: list[Literal["out/output.step", "cad/native", "plan/re_plan.vN.json"]]
    instructions: str | None = None


# ------------------------------------------------------------------------------ RE Plan IR
class Datum(_Closed):
    id: str
    role: Literal["primary", "secondary", "tertiary"]
    kind: Literal["plane", "axis", "point"]
    origin: Vec3
    direction: Vec3
    basis: str | None = None
    x_dir: Vec3 | None = None


class Parameter(_Closed):
    name: Identifier
    value: float
    measured: float | None = None
    snap_source: str | None = None


class ParamRef(_Closed):
    ref: str


# order matters for pydantic "smart" unions only as a tie-break; exact-type matches win
ParamValue = bool | int | float | str | ParamRef | list[Any]


class Line(_Closed):
    kind: Literal["line"]
    id: str | None = None
    p1: Vec2
    p2: Vec2
    construction: bool | None = None


class Arc(_Closed):
    kind: Literal["arc"]
    id: str | None = None
    center: Vec2
    radius: float
    start_deg: float
    end_deg: float
    construction: bool | None = None


class Circle(_Closed):
    kind: Literal["circle"]
    id: str | None = None
    center: Vec2
    radius: float
    construction: bool | None = None


class Rect(_Closed):
    kind: Literal["rect"]
    id: str | None = None
    center: Vec2
    width: float
    height: float
    angle_deg: float | None = None


class Slot(_Closed):
    kind: Literal["slot"]
    id: str | None = None
    c1: Vec2
    c2: Vec2
    radius: float


class Polygon(_Closed):
    kind: Literal["polygon"]
    id: str | None = None
    points: Annotated[list[Vec2], Field(min_length=3)]


class Spline(_Closed):
    kind: Literal["spline"]
    id: str | None = None
    points: Annotated[list[Vec2], Field(min_length=2)]
    closed: bool | None = None


SketchEntity = Annotated[
    Line | Arc | Circle | Rect | Slot | Polygon | Spline, Field(discriminator="kind")
]


class Sketch(_Closed):
    plane: Selector
    entities: Annotated[list[SketchEntity], Field(min_length=1)]
    constraints: list[str] | None = None


class Evidence(_Closed):
    source: Literal["fit_primitives", "slice", "detect_holes", "visual", "prior", "gt"] | None = (
        None
    )
    inlier_pct: float | None = None
    residual_mm: float | None = None
    note: str | None = None
    region_id: str | None = None


class Feature(_Closed):
    id: str
    type: FeatureType  # type: ignore[valid-type]
    operation: Literal["new", "add", "cut", "intersect"] = "add"
    sketch: Sketch | None = None
    params: dict[str, ParamValue]
    refs: list[Selector] | None = None
    evidence: Evidence | None = None


class ReferencePlane(_Closed):
    id: str
    origin: Vec3
    normal: Vec3
    x_dir: Vec3
    basis: str | None = None


class RePlan(_ClosedDocument):
    """`plan/re_plan.vN.json` (and GT `gt_plan.json`) — the L3→L2 seam."""

    spec_version: Literal["0.2.0"]
    part_id: str
    revision: Annotated[int, Field(ge=1)]
    units: Literal["mm"]
    part_family: str | None = None
    datums: Annotated[list[Datum], Field(min_length=1, max_length=3)]
    parameters: list[Parameter] | None = None
    features: Annotated[list[Feature], Field(min_length=1)]
    change_log: list[str] | None = None
    reference_planes: list[ReferencePlane] | None = None


# ----------------------------------------------------------------------------- key dims / GT
class KeyDimMeasure(_Open):
    face_type: Literal["plane", "cylinder", "cone", "sphere", "torus"] | None = None
    near_point: Vec3 | None = None
    axis_dir: Vec3 | None = None
    second: dict[str, Any] | None = None


class KeyDim(_Open):
    name: Identifier
    kind: Literal[
        "diameter", "radius", "face_distance", "axis_distance", "axis_to_face", "angle", "depth"
    ]
    nominal: float
    standard: bool = False
    measure: KeyDimMeasure
    editable: bool = False


class KeyDims(_Document):
    """`key_dims.yaml` (GT, private)."""

    part_id: str
    dims: Annotated[list[KeyDim], Field(min_length=3, max_length=12)]


class Contamination(_Open):
    public_scan: bool
    public_cad: bool
    commercial_replica: bool
    note: str | None = None

    @property
    def any(self) -> bool:
        return self.public_scan or self.public_cad or self.commercial_replica


class GtScan(_Open):
    file: str
    sha256: str
    scanner: str | None = None
    mode: str | None = None
    sessions: int | None = None
    date: str | None = None
    triangles: int | None = None
    known_defects: list[str] | None = None


Mat4 = Annotated[
    list[Annotated[list[float], Field(min_length=4, max_length=4)]],
    Field(min_length=4, max_length=4),
]


class Registration(_Open):
    T_scan_to_gt: Mat4
    method: str
    rmse_mm: float
    converged: bool
    seed: int | None = None
    iterations: int | None = None


class ScanFloor(_Open):
    scan_to_gt_p50_mm: float | None = None
    scan_to_gt_p95_mm: float
    gt_to_scan_observable_p95_mm: float
    max_mm: float | None = None


class Observability(_Open):
    observable_fraction: Annotated[float, Field(ge=0, le=1)]
    threshold_mm: float
    mask_file: str | None = None


class GtMeta(_Document):
    """`meta.yaml` (GT, private)."""

    part_id: str
    tolerance_class: ToleranceClass
    tier: Tier
    split: Split
    n_ref: int
    faces_gt: int
    face_types: dict[str, float]
    b1: int
    family: str | None = None
    source: str | None = None
    tessellation_chord_mm: float | None = None
    notes: str | None = None
    source_kind: Literal["print_scan", "scan_rebuild", "synthetic"]
    gt_author: Literal["human", "agent", "agent_human_reviewed"]
    contamination: Contamination
    scan: GtScan | None = None
    registration: Registration | None = None
    scan_floor: ScanFloor | None = None
    observability: Observability | None = None


# ---------------------------------------------------------------------------------- features
class ExtractedFeature(_Open):
    native_name: str | None = None
    native_type: str
    taxonomy_type: str
    counted: bool
    params: dict[str, Any] | None = None


class NativeParameter(_Open):
    name: str | None = None
    value: float | None = None
    feature: str | None = None


class Features(_Document):
    """`features.json` — adapter output (never agent self-report)."""

    tool: str
    native_file: str
    count: int
    features: list[ExtractedFeature]
    unmapped: list[str] | None = None
    native_sha256: str | None = None
    face_count: int | None = None
    parameters: list[NativeParameter] | None = None


# --------------------------------------------------------------------------- backend manifest
class BackendSoftware(_Open):
    name: str
    version: str
    source: str | None = None


class McpCommand(_Open):
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, Any] | None = None


class CadPrefixes(_Open):
    all: list[str] | None = None
    mutating: list[str] | None = None


class BackendInterface(_Open):
    kind: Literal["mcp", "cli"]
    mcp: McpCommand | None = None
    cad_prefixes: CadPrefixes | None = None
    allowed_commands: list[str] | None = None


class AdapterCli(_Open):
    export: str
    extract: str
    set_param: str
    replay: str
    execute_ir: str | None = None


class Conformance(_Open):
    passed: int | None = None
    total: int | None = None
    date: str | None = None
    software_version: str | None = None


class BackendManifest(_Document):
    """`backends/<b>/backend.yaml` — L1 pin."""

    id: BackendId
    software: BackendSoftware
    interface: BackendInterface
    hosts: list[Literal["windows", "linux", "macos", "termux"]]
    adapter_cli: AdapterCli
    max_parallel: Annotated[int, Field(ge=1)]
    conformance: Conformance | None = None


# -------------------------------------------------------------------------- skillset manifest
class SkillsetBackend(_Open):
    id: BackendId
    software_versions: str | None = None


class SkillsetManifest(_Document):
    """`recadbench-cad-skills/<b>/skillset.yaml` — L2 bundle."""

    id: str
    version: Annotated[str, StringConstraints(pattern=r"^v[0-9]+$")]
    backend: SkillsetBackend
    skills: list[str]
    agents: list[str] | None = None
    lib: list[str] | None = None
    shared: list[str] = Field(
        default_factory=lambda: ["_shared/ir_execution.md", "_shared/single_solid_rules.md"]
    )
    changelog: str | None = None


# -------------------------------------------------------------------------- strategy manifest
RetkTool = Literal[
    "mesh_stats",
    "slice",
    "fit_primitives",
    "detect_holes",
    "snap_nominal",
    "self_deviation",
    "render",
]


class SnapPolicy(_Open):
    A: float | None = None
    B: float | None = None
    C: float | None = None


class StrategyManifest(_Document):
    """`recadbench-strategies/strategies/<id>/strategy.yaml` — L3 bundle."""

    id: Annotated[str, StringConstraints(pattern=r"^(oracle|[A-Z])$")]
    name: str
    version: str
    playbook: str
    stages: list[str]
    toolkit_tools: list[RetkTool]
    max_refine_iters: int | None = None
    feature_budget_policy: Literal["none", "soft", "hard"] | None = None
    snap_policy: SnapPolicy | None = None
    vision: bool | None = None
    inject_gt_plan: bool | None = None
    skill: str


# ------------------------------------------------------------------------------ run manifest
class GitPin(_Open):
    id: str
    git_sha: str


class OptionalGitPin(_Open):
    id: str
    git_sha: str | None = None


class ModelPin(_Open):
    id: str
    provider: str
    temperature: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class RunBudgets(_Open):
    max_wall_s: int | None = None
    max_turns: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None


class RunBackend(_Open):
    id: Literal["nx", "build123d", "solidcraft", "freecad", "none"]
    software_version: str | None = None
    backend_sha: str | None = None


class RunManifest(_Document):
    """`run_manifest.json` — full pin of one run (00_DECISIONS D5)."""

    run_id: str
    track: Track
    part_id: str
    split: Split | None = None
    strategy: GitPin
    model: ModelPin
    agent_backend: Literal["claude_agent_sdk", "litellm_loop"] | None = None
    spec_version: str
    evaluator_version: str | None = None
    budgets: RunBudgets
    worker_host: str | None = None
    replicate: Annotated[int, Field(ge=0)]
    harness_level: Literal["H0", "H1", "H2", "H3"]
    feature_budget_hint: bool = False
    backend: RunBackend
    skillset: OptionalGitPin


# ------------------------------------------------------------------------------------ result
class Gate(_Open):
    ok: bool
    reason: str | None = None
    faces_pred: int | None = None
    faces_gt: int | None = None


class Alignment(_Open):
    rmse: float | None = None
    datum_offset_mm: float | None = None
    datum_offset_deg: float | None = None


class Metrics(_Open):
    p_tight: float | None = None
    p_wide: float | None = None
    mean: float | None = None
    rms: float | None = None
    p95: float | None = None
    max: float | None = None
    hausdorff: float | None = None
    r_feat: float | None = None
    face_ratio: float | None = None
    key_dims_ok: float | None = None
    face_type_tvd: float | None = None
    ir_adherence: float | None = None
    plan_drift: float | None = None
    param_coverage: float | None = None
    b1_match: bool | None = None
    p_tight_inferred: float | None = None
    p_wide_inferred: float | None = None
    band_tight_eff_mm: float | None = None
    band_wide_eff_mm: float | None = None


class Scores(_Open):
    S1: float | None = None
    S2: float | None = None
    S3: float | None = None
    S4: float | None = None
    S5: float | None = None
    total: float | None = None
    capped: bool | None = None
    plan_score: float | None = None


class Efficiency(_Open):
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    wall_s: float | None = None
    turns: int | None = None
    tool_errors: int | None = None


class Versions(_Open):
    spec: str
    evaluator: str


class LooseRef(_Open):
    id: str | None = None
    git_sha: str | None = None


class LooseModel(_Open):
    id: str | None = None
    provider: str | None = None


class LooseBackend(_Open):
    id: str | None = None
    software_version: str | None = None


class Result(_Document):
    """`result.json` — evaluator output."""

    run_id: str
    gate: Gate
    alignment: Alignment | None = None
    metrics: Metrics | None = None
    scores: Scores
    pass_: bool = Field(alias="pass")
    efficiency: Efficiency | None = None
    versions: Versions
    track: Track
    part_id: str
    replicate: int | None = None
    strategy: LooseRef
    model: LooseModel
    backend: LooseBackend
    skillset: LooseRef


# schema name (file stem without ".schema.json") → model
MODELS: dict[str, type[BaseModel]] = {
    "task": Task,
    "re_plan_ir": RePlan,
    "key_dims": KeyDims,
    "gt_meta": GtMeta,
    "features": Features,
    "backend_manifest": BackendManifest,
    "skillset_manifest": SkillsetManifest,
    "strategy_manifest": StrategyManifest,
    "run_manifest": RunManifest,
    "result": Result,
}
