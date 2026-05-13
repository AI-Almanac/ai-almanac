# Workflow IR Prototype Design

## Goal

AI Almanac should let climate researchers describe workflows in climate-science terms:
observations, model outputs, blends, benchmarks, forecasts, metrics, and forecast products.
The website should hide execution details such as Modal jobs, Globus Flows states, transfer
tasks, and cloud infrastructure unless the user needs them for debugging or provenance.

The workflow graph UI should serialize to an internal Workflow IR. Execution systems such as
Globus Flows, direct Modal execution, Globus Compute, or future cloud providers should be
compiler targets for that IR.

## Design Principles

- Keep the user-facing model domain-specific and execution-agnostic.
- Make the backend the authoritative owner of workflow semantics.
- Let the frontend render and edit workflows from backend-provided schema definitions.
- Keep nodes coarse enough to represent meaningful climate workflow steps.
- Treat port types as the interface contract between nodes.
- Make it easy to add new node types, port types, config fields, and execution targets.
- Validate workflow intent before compiling to a target.
- Compile only executable workflow parts; purely configurational UI nodes may become action
  parameters rather than execution steps.

## Core Concepts

### Ownership Boundary

The backend owns the workflow data model. This includes node type definitions, port type
definitions, config schemas, validation rules, IR versioning, and execution-target compilation.

The frontend owns the editing experience. It should provide a polished node graph UI, render
forms from schema definitions, maintain local draft state, and show client-side validation
feedback. It should not be the source of truth for what makes a workflow valid.

```text
backend
  owns workflow semantics, validation, compilation, execution, provenance

frontend
  owns interaction design, layout, styling, draft editing, user feedback
```

The frontend may do optimistic validation for a better user experience, but the backend must
re-validate submitted workflows before saving, compiling, or executing them.

### Workflow IR

The IR is the stable contract between the node editor, backend validation, and execution
target compilers.

```ts
type WorkflowIR = {
  version: '0.1';
  graph: {
    nodes: IRNode[];
    edges: IREdge[];
  };
  run: {
    mode: 'interactive' | 'scheduled';
  };
};
```

### Nodes

Nodes represent climate workflow concepts or transforms. A node owns config and declares its
typed inputs and outputs.

```ts
type IRNode = {
  id: string;
  type: NodeType;
  label: string;
  config: Record<string, unknown>;
  inputs: IRPort[];
  outputs: IRPort[];
};
```

Example node types:

- `model_output_source`
- `observation_dataset`
- `region_event_definition`
- `evaluation_window`
- `blend`
- `benchmark`
- `forecast`
- `result_package`

### Ports

Ports define what a node consumes or emits. Port compatibility should usually be based on
matching `type`.

```ts
type IRPort = {
  id: string;
  label: string;
  type: PortType;
  required: boolean;
  multiple: boolean;
};
```

Initial port types:

- `model-output`
- `observation-dataset`
- `region-event-config`
- `evaluation-window`
- `onset-rules`
- `spatial-mask`
- `benchmark-results`
- `forecast-products`

Blending is a transform:

```text
model-output -> blend -> model-output
```

This keeps benchmark and forecast nodes simple: they consume `model-output` regardless of
whether it came from raw model data, preprocessing, or blending.

### Edges

Edges connect one output port to one input port.

```ts
type IREdge = {
  id: string;
  source: {
    nodeId: string;
    portId: string;
  };
  target: {
    nodeId: string;
    portId: string;
  };
};
```

## Validation

Validation should be explicit and layered.

Graph validation:

- All referenced nodes and ports exist.
- Edges connect output ports to input ports.
- Connected ports have compatible types.
- Required inputs are satisfied.
- The graph is acyclic unless a future node type explicitly supports iteration.

Config validation:

- Required config fields are present.
- Config values match the node schema.
- Artifact references are resolvable.

Target validation:

- The selected execution target supports every executable node.
- The graph shape can compile to that target.
- Required target-specific runtime settings are present.

## Execution Targets

The IR should not expose target-specific details. Backends compile IR into target-specific
plans.

Potential targets:

- Globus Flows
- Direct Modal execution
- Globus Compute
- Future workflow engines or cloud batch systems

### Globus Flows Target

Globus Flows is a likely first production target for coarse-grained orchestration. The compiler
can translate executable IR nodes into Globus Flow `Action` states and pass config/artifact
references through JSON payloads.

AI Almanac can expose a FastAPI-backed Globus Action Provider interface. That provider can
reuse existing Modal submission, polling, and cancellation logic.

Example shape:

```text
WorkflowIR
  -> validate
  -> compile to Globus Flow JSON
  -> Action states call AI Almanac action provider
  -> provider starts/polls/cancels Modal jobs
```

## Near-Term Prototype Scope

The prototype should focus on:

- Node graph UI with typed ports and configurable nodes.
- A small shared registry for node templates and port types.
- Frontend serialization to Workflow IR.
- Backend validation of the IR.
- One simple execution path, likely `run_benchmark`.

Scheduling, operational forecasting, dissemination, and target-specific optimization should stay
out of the first prototype, but the IR should leave room for them.

Future scheduled runs may extend `run`:

```ts
run: {
  mode: 'scheduled';
  schedule: {
    cron: string;
    timezone: string;
  };
}
```

## Schema Discovery

The frontend should eventually build the node palette, port colors, handle labels, and config
forms from a backend schema endpoint.

```http
GET /workflow/schema
```

Example response shape:

```json
{
  "version": "0.1",
  "portTypes": {
    "model-output": {
      "label": "Model Output",
      "color": "#79c2ff",
      "description": "Forecast or hindcast model output, raw or transformed."
    }
  },
  "nodeTypes": {
    "blend": {
      "label": "Blend",
      "category": "Transform",
      "description": "Combine model outputs into a blended model output.",
      "inputs": [
        {
          "id": "model_input",
          "label": "Model Output",
          "type": "model-output",
          "required": true,
          "multiple": true
        }
      ],
      "outputs": [
        {
          "id": "model_output",
          "label": "Blended Output",
          "type": "model-output",
          "required": true,
          "multiple": false
        }
      ],
      "configSchema": {
        "type": "object",
        "properties": {
          "method": {
            "type": "string",
            "enum": ["weighted_mean", "median"]
          }
        },
        "required": ["method"]
      }
    }
  }
}
```

For the current prototype, frontend-local definitions are acceptable. The next architectural step
should be moving those definitions behind the backend schema endpoint.

## Open Questions

- Which nodes are purely configurational versus executable?
- How should artifact references be represented across Globus Transfer, object storage, and
  Modal-accessible paths?
- Should blends be stored as versioned artifacts, executable recipes, or both?
- How much parallelism should be represented in the IR versus hidden inside Modal actions?
- What provenance metadata should every node execution emit?
