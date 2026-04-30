import uuid
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..auth import CurrentUser
from ..config import get_model_registry, get_demo_datasets, get_romp_defaults
from .jobs import JobCreate, JobOut, RompParams, create_job

router = APIRouter(prefix="/workflow", tags=["workflow"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IR types
# ---------------------------------------------------------------------------


class IRPort(BaseModel):
    id: str
    label: str
    type: str
    required: bool = True
    multiple: bool = False


class IRNode(BaseModel):
    id: str
    type: str
    label: str
    config: dict = {}
    inputs: list[IRPort] = []
    outputs: list[IRPort] = []


class IREdge(BaseModel):
    id: str
    source: dict  # {nodeId, portId}
    target: dict  # {nodeId, portId}


class IRGraph(BaseModel):
    nodes: list[IRNode]
    edges: list[IREdge]


class IRRun(BaseModel):
    mode: Literal["interactive", "scheduled"] = "interactive"


class WorkflowIR(BaseModel):
    version: Literal["0.1"] = "0.1"
    graph: IRGraph
    run: IRRun = IRRun()


# ---------------------------------------------------------------------------
# Validation types
# ---------------------------------------------------------------------------


class ValidationError(BaseModel):
    node_id: str | None = None
    field: str | None = None
    message: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationError]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

PORT_TYPES = {
    "model-output": {
        "label": "Model Predictions",
        "color": "#79c2ff",
        "description": "Forecast or hindcast model predictions, raw or blended.",
    },
    "observation-dataset": {
        "label": "Observation Dataset",
        "color": "#f0c66f",
        "description": "Ground-truth observational data.",
    },
    "region-event-config": {
        "label": "Region + Event Config",
        "color": "#c8a8ff",
        "description": "Bound region and event type definition.",
    },
    "evaluation-window": {
        "label": "Evaluation Window",
        "color": "#f4b8a8",
        "description": "Date range and climatology years for evaluation.",
    },
    "onset-rules": {
        "label": "Onset Rules",
        "color": "#ff9fc4",
        "description": "Wet/dry spell thresholds and threshold files.",
    },
    "spatial-mask": {
        "label": "Spatial Mask",
        "color": "#9ee37d",
        "description": "NetCDF mask for excluding grid cells.",
    },
    "benchmark-results": {
        "label": "Benchmark Results",
        "color": "#d4933f",
        "description": "Metric outputs from a completed benchmark run.",
    },
}

NODE_TYPES = {
    "model_output_source": {
        "label": "Model Predictions",
        "category": "Input",
        "description": "Select one or more model prediction streams to benchmark.",
        "inputs": [],
        "outputs": [
            {"id": "model_output", "label": "Predictions", "type": "model-output", "required": True, "multiple": True}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "model_names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["model_names"],
        },
    },
    "observation_dataset": {
        "label": "Observation Dataset",
        "category": "Input",
        "description": "Select the ground-truth observation dataset.",
        "inputs": [],
        "outputs": [
            {"id": "observations", "label": "Observations", "type": "observation-dataset", "required": True, "multiple": False}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
            },
            "required": ["dataset_id"],
        },
    },
    "region_event_definition": {
        "label": "Region + Event Definition",
        "category": "Config",
        "description": "Bind the run to a ROMP region and event type.",
        "inputs": [],
        "outputs": [
            {"id": "region_event_config", "label": "Region Event", "type": "region-event-config", "required": True, "multiple": False}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "enum": ["india", "ethiopia", "test"]},
                "event_type": {"type": "string", "enum": ["monsoon_onset", "monsoon_cessation"]},
            },
            "required": ["region", "event_type"],
        },
    },
    "evaluation_window": {
        "label": "Evaluation Window",
        "category": "Config",
        "description": "Date range and climatology years for each model.",
        "inputs": [
            {"id": "model_output", "label": "Predictions", "type": "model-output", "required": True, "multiple": False}
        ],
        "outputs": [
            {"id": "evaluation_window", "label": "Eval Window", "type": "evaluation-window", "required": True, "multiple": False}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "start_year_clim": {"type": "integer"},
                "end_year_clim": {"type": "integer"},
                "init_days": {"type": "string"},
            },
        },
    },
    "onset_rules": {
        "label": "Onset Threshold Rules",
        "category": "Config",
        "description": "Wet/dry spell detection thresholds.",
        "inputs": [],
        "outputs": [
            {"id": "onset_rules", "label": "Onset Rules", "type": "onset-rules", "required": False, "multiple": False}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "wet_threshold": {"type": "number"},
                "wet_spell": {"type": "integer"},
                "dry_spell": {"type": "integer"},
                "thresh_file": {"type": "string"},
            },
        },
    },
    "spatial_mask": {
        "label": "Spatial Mask",
        "category": "Config",
        "description": "NetCDF mask to exclude grid cells from metrics.",
        "inputs": [],
        "outputs": [
            {"id": "spatial_mask", "label": "Mask", "type": "spatial-mask", "required": False, "multiple": False}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "nc_mask": {"type": "string"},
            },
        },
    },
    "benchmark": {
        "label": "Benchmark Run",
        "category": "Execution",
        "description": "Compile inputs and submit benchmark jobs.",
        "inputs": [
            {"id": "model_output", "label": "Predictions", "type": "model-output", "required": True, "multiple": True},
            {"id": "observations", "label": "Observations", "type": "observation-dataset", "required": True, "multiple": False},
            {"id": "region_event_config", "label": "Region Event", "type": "region-event-config", "required": True, "multiple": False},
            {"id": "evaluation_window", "label": "Eval Window", "type": "evaluation-window", "required": False, "multiple": False},
            {"id": "onset_rules", "label": "Onset Rules", "type": "onset-rules", "required": False, "multiple": False},
            {"id": "spatial_mask", "label": "Mask", "type": "spatial-mask", "required": False, "multiple": False},
        ],
        "outputs": [
            {"id": "results", "label": "Benchmark Results", "type": "benchmark-results", "required": True, "multiple": False}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "parallel": {"type": "boolean"},
                "probabilistic": {"type": "boolean"},
                "max_forecast_day": {"type": "integer"},
                "members": {"type": "string"},
            },
        },
    },
}


@router.get("/schema")
def get_schema() -> dict:
    registry = get_model_registry()
    datasets = get_demo_datasets()
    defaults = get_romp_defaults()

    available_models = [
        {"id": m["id"], "display_name": m.get("display_name", m["id"]), "region": m.get("region", "")}
        for m in registry
    ]
    available_datasets = [
        {"id": d["id"], "name": d["name"], "region": d.get("region", "")}
        for d in datasets
    ]

    return {
        "version": "0.1",
        "portTypes": PORT_TYPES,
        "nodeTypes": NODE_TYPES,
        "availableModels": available_models,
        "availableDatasets": available_datasets,
        "rompDefaults": defaults,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

REQUIRED_NODE_TYPES = {"model_output_source", "observation_dataset", "region_event_definition", "benchmark"}


def _validate_ir(ir: WorkflowIR) -> list[ValidationError]:
    errors: list[ValidationError] = []
    node_map = {n.id: n for n in ir.graph.nodes}

    # Every node referenced in edges must exist
    for edge in ir.graph.edges:
        src_id = edge.source.get("nodeId")
        tgt_id = edge.target.get("nodeId")
        if src_id and src_id not in node_map:
            errors.append(ValidationError(message=f"Edge references unknown source node '{src_id}'"))
        if tgt_id and tgt_id not in node_map:
            errors.append(ValidationError(message=f"Edge references unknown target node '{tgt_id}'"))

    # Required node types must be present
    present_types = {n.type for n in ir.graph.nodes}
    for required in REQUIRED_NODE_TYPES:
        if required not in present_types:
            errors.append(ValidationError(message=f"Workflow is missing required node type '{required}'"))

    # Required config fields per node type
    for node in ir.graph.nodes:
        schema = NODE_TYPES.get(node.type, {})
        required_fields = schema.get("configSchema", {}).get("required", [])
        for field in required_fields:
            if field not in node.config or node.config[field] in (None, "", []):
                errors.append(ValidationError(node_id=node.id, field=field, message=f"Required field '{field}' is missing on node '{node.label}'"))

    # benchmark node must have model_output_source wired to it
    benchmark_nodes = [n for n in ir.graph.nodes if n.type == "benchmark"]
    model_source_nodes = {n.id for n in ir.graph.nodes if n.type == "model_output_source"}
    obs_nodes = {n.id for n in ir.graph.nodes if n.type == "observation_dataset"}
    region_nodes = {n.id for n in ir.graph.nodes if n.type == "region_event_definition"}

    for bm in benchmark_nodes:
        edge_sources = {e.source.get("nodeId") for e in ir.graph.edges if e.target.get("nodeId") == bm.id}
        if not edge_sources & model_source_nodes:
            errors.append(ValidationError(node_id=bm.id, message="Benchmark node has no model predictions connected"))
        if not edge_sources & obs_nodes:
            errors.append(ValidationError(node_id=bm.id, message="Benchmark node has no observation dataset connected"))
        if not edge_sources & region_nodes:
            errors.append(ValidationError(node_id=bm.id, message="Benchmark node has no region/event definition connected"))

    return errors


@router.post("/validate", response_model=ValidationResult)
def validate_workflow(ir: WorkflowIR) -> ValidationResult:
    errors = _validate_ir(ir)
    return ValidationResult(valid=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# Compile IR → JobCreate list
# ---------------------------------------------------------------------------


def _compile_ir(ir: WorkflowIR) -> list[JobCreate]:
    node_map = {n.id: n for n in ir.graph.nodes}

    benchmark_node = next((n for n in ir.graph.nodes if n.type == "benchmark"), None)
    if not benchmark_node:
        raise HTTPException(status_code=422, detail="No benchmark node found in workflow")

    # Find nodes connected to benchmark
    def connected_node(target_node_id: str, source_type: str) -> IRNode | None:
        for edge in ir.graph.edges:
            if edge.target.get("nodeId") == target_node_id:
                src = node_map.get(edge.source.get("nodeId", ""))
                if src and src.type == source_type:
                    return src
        return None

    model_source = connected_node(benchmark_node.id, "model_output_source")
    obs_node = connected_node(benchmark_node.id, "observation_dataset")
    region_node = connected_node(benchmark_node.id, "region_event_definition")
    eval_window = connected_node(benchmark_node.id, "evaluation_window")
    onset_rules = connected_node(benchmark_node.id, "onset_rules")
    mask_node = connected_node(benchmark_node.id, "spatial_mask")

    if not model_source:
        raise HTTPException(status_code=422, detail="No model predictions connected to benchmark")
    if not obs_node:
        raise HTTPException(status_code=422, detail="No observation dataset connected to benchmark")
    if not region_node:
        raise HTTPException(status_code=422, detail="No region/event definition connected to benchmark")

    dataset_id = obs_node.config.get("dataset_id", "")
    region = region_node.config.get("region", "")
    event_type = region_node.config.get("event_type", "")
    model_names: list[str] = model_source.config.get("model_names", [])

    if not model_names:
        raise HTTPException(status_code=422, detail="No models selected in model predictions node")

    run_id = str(uuid.uuid4())
    jobs = []
    for model_name in model_names:
        params = RompParams(
            region=region or None,
            event_type=event_type or None,  # type: ignore[call-arg]
        )
        if eval_window:
            cfg = eval_window.config
            params.start_date = cfg.get("start_date") or None
            params.end_date = cfg.get("end_date") or None
            params.start_year_clim = cfg.get("start_year_clim") or None
            params.end_year_clim = cfg.get("end_year_clim") or None
            params.init_days = cfg.get("init_days") or None
        if onset_rules:
            cfg = onset_rules.config
            params.wet_threshold = cfg.get("wet_threshold") or None
            params.wet_spell = cfg.get("wet_spell") or None
            params.dry_spell = cfg.get("dry_spell") or None
            params.thresh_file = cfg.get("thresh_file") or None
        if mask_node:
            params.nc_mask = mask_node.config.get("nc_mask") or None
        bm_cfg = benchmark_node.config
        params.parallel = bm_cfg.get("parallel") if bm_cfg.get("parallel") is not None else None
        params.probabilistic = bm_cfg.get("probabilistic") if bm_cfg.get("probabilistic") is not None else None
        params.max_forecast_day = bm_cfg.get("max_forecast_day") or None
        params.members = bm_cfg.get("members") or None

        jobs.append(JobCreate(
            dataset_id=dataset_id,
            model_name=model_name,
            params=params,
            run_id=run_id,
        ))
    return jobs


def _model_exists_for_job(job_create: JobCreate, registry: list[dict]) -> bool:
    region = (job_create.params.region or "").lower()
    return any(
        model["id"] == job_create.model_name and model.get("region", "").lower() == region
        for model in registry
    ) or any(model["id"] == job_create.model_name for model in registry)


def _validate_compiled_jobs(job_creates: list[JobCreate]) -> list[ValidationError]:
    registry = get_model_registry()
    return [
        ValidationError(
            field="model_names",
            message=f"Unknown model: {job_create.model_name!r}",
        )
        for job_create in job_creates
        if not _model_exists_for_job(job_create, registry)
    ]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@router.post("/run", response_model=list[JobOut], status_code=status.HTTP_201_CREATED)
async def run_workflow(ir: WorkflowIR, user: CurrentUser) -> list[JobOut]:
    errors = _validate_ir(ir)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=[e.model_dump(exclude_none=True) for e in errors],
        )

    job_creates = _compile_ir(ir)
    job_errors = _validate_compiled_jobs(job_creates)
    if job_errors:
        raise HTTPException(
            status_code=422,
            detail=[e.model_dump(exclude_none=True) for e in job_errors],
        )

    results = []
    for job_create in job_creates:
        job_out = await create_job(job_create, user)
        results.append(job_out)
    return results
