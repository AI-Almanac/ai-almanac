<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import {
		Background,
		BackgroundVariant,
		Controls,
		MiniMap,
		MarkerType,
		SvelteFlow,
		type Connection,
		type Edge,
		type Node
	} from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import WorkflowNode, {
		type WorkflowNodeData,
		type WorkflowPort,
		type WorkflowPortType
	} from './WorkflowNode.svelte';
	import {
		getWorkflowSchema,
		validateWorkflow,
		runWorkflow,
		type WorkflowSchema,
		type WorkflowIR,
		type IRNode,
		type IREdge,
		type Job
	} from '$lib/api';

	// ---------------------------------------------------------------------------
	// Types
	// ---------------------------------------------------------------------------

	type FlowNode = Node<WorkflowNodeData, 'workflow'>;

	type PaletteEntry = {
		type: string;
		label: string;
		category: string;
		description: string;
		required: boolean;
		addAction: string;
		defaultPosition: { x: number; y: number };
	};

	type WorkflowGraphDocument = {
		version: '0.1';
		graph: { nodes: IRNode[]; edges: IREdge[] };
		runConfig: WorkflowRunConfig;
		metadata: { name: string };
	};

	type WorkflowRunConfig = {
		mode: 'interactive' | 'scheduled';
		parallel: boolean;
		probabilistic: boolean;
		maxForecastDay?: number;
		members?: string;
	};

	type ClientValidationIssue = {
		node_id?: string;
		field?: string;
		port_id?: string;
		message: string;
	};

	// ---------------------------------------------------------------------------
	// Node palette definition
	// ---------------------------------------------------------------------------

	const PALETTE: PaletteEntry[] = [
		{
			type: 'region_event_definition',
			label: 'Region + Event',
			category: 'Required inputs',
			description: 'Set the region and event type for the benchmark.',
			required: true,
			addAction: 'Choose region/event',
			defaultPosition: { x: 40, y: 80 }
		},
		{
			type: 'model_output_source',
			label: 'Model Predictions',
			category: 'Required inputs',
			description: 'Select one or more models for the chosen region.',
			required: true,
			addAction: 'Select predictions',
			defaultPosition: { x: 40, y: 280 }
		},
		{
			type: 'observation_dataset',
			label: 'Observation Dataset',
			category: 'Required inputs',
			description: 'Select observations for the chosen region.',
			required: true,
			addAction: 'Select dataset',
			defaultPosition: { x: 40, y: 480 }
		},
		{
			type: 'evaluation_window',
			label: 'Evaluation Window',
			category: 'Optional inputs',
			description: 'Date range and climatology years. Uses model defaults if omitted.',
			required: false,
			addAction: 'Add eval window',
			defaultPosition: { x: 360, y: 80 }
		},
		{
			type: 'onset_rules',
			label: 'Onset Threshold Rules',
			category: 'Optional inputs',
			description: 'Wet/dry spell thresholds. Uses ROMP defaults if omitted.',
			required: false,
			addAction: 'Add onset rules',
			defaultPosition: { x: 360, y: 280 }
		},
		{
			type: 'spatial_mask',
			label: 'Spatial Mask',
			category: 'Optional inputs',
			description: 'NetCDF mask to exclude grid cells. Optional.',
			required: false,
			addAction: 'Add spatial mask',
			defaultPosition: { x: 360, y: 480 }
		}
	];

	// ---------------------------------------------------------------------------
	// Port colors and helpers
	// ---------------------------------------------------------------------------

	const portColors: Record<WorkflowPortType, string> = {
		'model-output': '#79c2ff',
		'observation-dataset': '#f0c66f',
		'region-event-config': '#c8a8ff',
		'evaluation-window': '#f4b8a8',
		'onset-rules': '#ff9fc4',
		'spatial-mask': '#9ee37d',
		'benchmark-results': '#d4933f',
		'forecast-products': '#70d6a6',
		control: '#82a8c8'
	};

	function port(id: string, label: string, type: WorkflowPortType, required = false): WorkflowPort {
		return { id, label, type, required };
	}

	function makeNodeData(type: string): WorkflowNodeData {
		switch (type) {
			case 'model_output_source':
				return {
					label: 'Model Predictions',
					kind: 'catalog',
					phase: 'Input',
					description: 'Select one or more models to benchmark.',
					status: 'draft',
					chips: [],
					inputs: [],
					outputs: [port('model_output', 'Predictions', 'model-output', true)]
				};
			case 'observation_dataset':
				return {
					label: 'Observation Dataset',
					kind: 'catalog',
					phase: 'Input',
					description: 'Select the ground-truth observation dataset.',
					status: 'draft',
					chips: [],
					inputs: [],
					outputs: [port('observations', 'Observations', 'observation-dataset', true)]
				};
			case 'region_event_definition':
				return {
					label: 'Region + Event',
					kind: 'preprocess',
					phase: 'Input',
					description: 'Bind the run to a region and event type.',
					status: 'draft',
					chips: [],
					inputs: [],
					outputs: [port('region_event_config', 'Region Event', 'region-event-config', true)]
				};
			case 'evaluation_window':
				return {
					label: 'Evaluation Window',
					kind: 'preprocess',
					phase: 'Configuration',
					description: 'Date range and climatology years.',
					status: 'draft',
					chips: ['optional'],
					inputs: [],
					outputs: [port('evaluation_window', 'Eval Window', 'evaluation-window')]
				};
			case 'onset_rules':
				return {
					label: 'Onset Threshold Rules',
					kind: 'preprocess',
					phase: 'Configuration',
					description: 'Wet/dry spell detection thresholds.',
					status: 'draft',
					chips: ['optional'],
					inputs: [],
					outputs: [port('onset_rules', 'Onset Rules', 'onset-rules')]
				};
			case 'spatial_mask':
				return {
					label: 'Spatial Mask',
					kind: 'preprocess',
					phase: 'Configuration',
					description: 'NetCDF mask to exclude grid cells.',
					status: 'draft',
					chips: ['optional'],
					inputs: [],
					outputs: [port('spatial_mask', 'Mask', 'spatial-mask')]
				};
			default:
				throw new Error(`Unknown node type: ${type}`);
		}
	}

	const BENCHMARK_NODE: FlowNode = {
		id: 'benchmark',
		type: 'workflow',
		position: { x: 700, y: 240 },
		deletable: false,
		data: {
			label: 'Benchmark Run',
			kind: 'benchmark',
			phase: 'Execution',
			description: 'Compile config and submit benchmark jobs.',
			status: 'incomplete',
			chips: [],
			inputs: [
				port('model_output', 'Predictions', 'model-output', true),
				port('observations', 'Observations', 'observation-dataset', true),
				port('region_event_config', 'Region Event', 'region-event-config', true),
				port('evaluation_window', 'Eval Window', 'evaluation-window'),
				port('onset_rules', 'Onset Rules', 'onset-rules'),
				port('spatial_mask', 'Mask', 'spatial-mask')
			],
			outputs: [port('results', 'Benchmark Results', 'benchmark-results')]
		}
	};

	// ---------------------------------------------------------------------------
	// Graph state
	// ---------------------------------------------------------------------------

	let graphNodes = $state<FlowNode[]>([BENCHMARK_NODE]);
	let graphEdges = $state<Edge[]>([]);
	let graphRevision = $state(0);
	let selectedNodeId = $state<string | null>(null);

	function bumpGraphRevision() {
		graphRevision += 1;
	}

	function onDelete({ nodes }: { nodes: FlowNode[]; edges: Edge[] }) {
		const deletedIds = new Set(nodes.map((n) => n.id));
		if (selectedNodeId && deletedIds.has(selectedNodeId)) selectedNodeId = null;
		if (deletedIds.size > 0) bumpGraphRevision();
	}

	function isValidConnection(connection: Connection | Edge): boolean {
		const srcNode = graphNodes.find((n) => n.id === connection.source);
		const tgtNode = graphNodes.find((n) => n.id === connection.target);
		const srcPort = srcNode?.data.outputs?.find((p) => p.id === (connection.sourceHandle ?? ''));
		const tgtPort = tgtNode?.data.inputs?.find((p) => p.id === (connection.targetHandle ?? ''));
		if (!srcPort || !tgtPort) return false;
		return srcPort.type === tgtPort.type;
	}

	function onBeforeConnect(connection: Connection): Edge | Connection {
		const sourceNode = graphNodes.find((n) => n.id === connection.source);
		const sourcePort = sourceNode?.data.outputs?.find((o) => o.id === connection.sourceHandle);
		const color = sourcePort ? portColors[sourcePort.type] : portColors.control;
		return {
			...connection,
			id: `${connection.source}-${connection.sourceHandle}-${connection.target}-${connection.targetHandle}`,
			type: 'smoothstep',
			animated: true,
			markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
			style: `stroke: ${color}; stroke-width: 2;`
		};
	}

	// ---------------------------------------------------------------------------
	// Node palette: add / remove
	// ---------------------------------------------------------------------------

	function nodeTypeInGraph(type: string): boolean {
		return graphNodes.some((n) => n.id === type);
	}

	function addNodeToGraph(entry: PaletteEntry) {
		if (nodeTypeInGraph(entry.type)) return;
		const newNode: FlowNode = {
			id: entry.type,
			type: 'workflow',
			position: { ...entry.defaultPosition },
			data: makeNodeData(entry.type)
		};
		graphNodes = [...graphNodes, newNode];
		selectedNodeId = entry.type;
		bumpGraphRevision();
	}

	function removeNodeFromGraph(type: string) {
		graphNodes = graphNodes.filter((n) => n.id !== type);
		graphEdges = graphEdges.filter(
			(e) => e.source !== type && e.target !== type
		);
		if (selectedNodeId === type) selectedNodeId = null;
		bumpGraphRevision();
	}

	// ---------------------------------------------------------------------------
	// Per-node configs (persist across add/remove)
	// ---------------------------------------------------------------------------

	type Configs = {
		model_output_source: { model_names: string[] };
		observation_dataset: { dataset_id: string };
		region_event_definition: { region: string; event_type: string };
		evaluation_window: {
			start_date: string;
			end_date: string;
			start_year_clim: string;
			end_year_clim: string;
			init_days: string;
		};
		onset_rules: {
			wet_threshold: string;
			wet_spell: string;
			dry_spell: string;
			thresh_file: string;
		};
		spatial_mask: { nc_mask: string };
		benchmark: {
			parallel: boolean;
			probabilistic: boolean;
			max_forecast_day_mode: 'default' | 'custom';
			max_forecast_day: string;
			members: string;
		};
	};

	let configs = $state<Configs>({
		model_output_source: { model_names: [] },
		observation_dataset: { dataset_id: '' },
		region_event_definition: { region: '', event_type: '' },
		evaluation_window: { start_date: '', end_date: '', start_year_clim: '', end_year_clim: '', init_days: '' },
		onset_rules: { wet_threshold: '', wet_spell: '', dry_spell: '', thresh_file: '' },
		spatial_mask: { nc_mask: '' },
		benchmark: {
			parallel: true,
			probabilistic: false,
			max_forecast_day_mode: 'default',
			max_forecast_day: '',
			members: ''
		}
	});

	// ---------------------------------------------------------------------------
	// Schema
	// ---------------------------------------------------------------------------

	let schema = $state<WorkflowSchema | null>(null);
	let schemaError = $state<string | null>(null);

	onMount(async () => {
		try {
			schema = await getWorkflowSchema();
		} catch (err) {
			schemaError = err instanceof Error ? err.message : String(err);
		}
	});

	// ---------------------------------------------------------------------------
	// Derived: workflow context
	// ---------------------------------------------------------------------------

	const activeRegion = $derived((): string => {
		return configs.region_event_definition.region;
	});

	const filteredModels = $derived(() => {
		if (!schema) return [];
		const region = activeRegion();
		if (!region) return [];
		return schema.availableModels.filter((m) => m.region === region);
	});

	const filteredDatasets = $derived(() => {
		if (!schema) return [];
		const region = activeRegion();
		if (!region) return [];
		return schema.availableDatasets.filter((d) => d.region === region);
	});

	let previousRegion = $state('');

	$effect(() => {
		const region = activeRegion();
		if (region === previousRegion) return;
		previousRegion = region;
		configs.model_output_source.model_names = [];
		configs.observation_dataset.dataset_id = '';
	});

	function toggleModel(id: string) {
		const current = configs.model_output_source.model_names;
		configs.model_output_source.model_names = current.includes(id)
			? current.filter((m) => m !== id)
			: [...current, id];
	}

	// ---------------------------------------------------------------------------
	// Client-side graph compiler and validation
	// ---------------------------------------------------------------------------

	function inputConnection(nodeId: string, portId: string): Edge | undefined {
		return graphEdges.find((e) => e.target === nodeId && e.targetHandle === portId);
	}

	function nodeConfigured(nodeId: string): boolean {
		switch (nodeId) {
			case 'model_output_source':
				return configs.model_output_source.model_names.length > 0;
			case 'observation_dataset':
				return configs.observation_dataset.dataset_id.length > 0;
			case 'region_event_definition':
				return Boolean(
					configs.region_event_definition.region && configs.region_event_definition.event_type
				);
			default:
				return true;
		}
	}

	function validateClientGraph(): ClientValidationIssue[] {
		const issues: ClientValidationIssue[] = [];
		const nodesById = new Map(graphNodes.map((n) => [n.id, n]));
		const requiredNodeLabels = new Map(PALETTE.filter((p) => p.required).map((p) => [p.type, p.label]));

		for (const [nodeId, label] of requiredNodeLabels) {
			if (!nodesById.has(nodeId)) {
				issues.push({ message: `${label} must be added before this workflow can run.` });
			}
		}

		if (nodesById.has('model_output_source') && !configs.model_output_source.model_names.length) {
			issues.push({
				node_id: 'model_output_source',
				field: 'model_names',
				message: 'Select at least one model prediction source.'
			});
		}
		if (nodesById.has('observation_dataset') && !configs.observation_dataset.dataset_id) {
			issues.push({
				node_id: 'observation_dataset',
				field: 'dataset_id',
				message: 'Select an observation dataset.'
			});
		}
		if (nodesById.has('region_event_definition')) {
			if (!configs.region_event_definition.region) {
				issues.push({
					node_id: 'region_event_definition',
					field: 'region',
					message: 'Select a region.'
				});
			}
			if (!configs.region_event_definition.event_type) {
				issues.push({
					node_id: 'region_event_definition',
					field: 'event_type',
					message: 'Select an event type.'
				});
			}
		}

		const benchmark = nodesById.get('benchmark');
		if (benchmark) {
			for (const input of benchmark.data.inputs ?? []) {
				if (!input.required) continue;
				const connection = inputConnection('benchmark', input.id);
				if (!connection) {
					issues.push({
						node_id: 'benchmark',
						port_id: input.id,
						message: `Connect ${input.label.toLowerCase()} to the Benchmark Run node.`
					});
					continue;
				}
				if (!nodesById.has(String(connection.source))) {
					issues.push({
						node_id: 'benchmark',
						port_id: input.id,
						message: `${input.label} is connected to a node that is no longer in the graph.`
					});
				}
			}
		}

		if (
			configs.benchmark.max_forecast_day_mode === 'custom' &&
			(!configs.benchmark.max_forecast_day || Number(configs.benchmark.max_forecast_day) < 1)
		) {
			issues.push({
				node_id: 'benchmark',
				field: 'max_forecast_day',
				message: 'Enter a max forecast day greater than zero, or use the model default.'
			});
		}

		return issues;
	}

	const edgeSignature = $derived(
		graphEdges
			.map((edge) => `${edge.source}:${edge.sourceHandle ?? ''}->${edge.target}:${edge.targetHandle ?? ''}`)
			.sort()
			.join('|')
	);

	const clientIssues = $derived.by(() => {
		void configs;
		void edgeSignature;
		void graphRevision;
		return untrack(validateClientGraph);
	});
	const runBlockingIssues = $derived(clientIssues);

	// ---------------------------------------------------------------------------
	// Config summary — synced into node data so the canvas shows current state
	// ---------------------------------------------------------------------------

	function buildConfigSummary(nodeId: string): { label: string; value: string }[] {
		switch (nodeId) {
			case 'model_output_source': {
				const names = configs.model_output_source.model_names;
				if (!names.length) return [];
				return [{ label: 'models', value: names.join(', ') }];
			}
			case 'observation_dataset': {
				const id = configs.observation_dataset.dataset_id;
				if (!id) return [];
				const name = schema?.availableDatasets.find((d) => d.id === id)?.name ?? id;
				return [{ label: 'dataset', value: name }];
			}
			case 'region_event_definition': {
				const { region, event_type } = configs.region_event_definition;
				const rows: { label: string; value: string }[] = [];
				if (region) rows.push({ label: 'region', value: region });
				if (event_type) rows.push({ label: 'event', value: event_type.replace('_', ' ') });
				return rows;
			}
			case 'evaluation_window': {
				const { start_date, end_date, start_year_clim, end_year_clim, init_days } = configs.evaluation_window;
				const rows: { label: string; value: string }[] = [];
				if (start_date && end_date) rows.push({ label: 'period', value: `${start_date} – ${end_date}` });
				else if (start_date) rows.push({ label: 'from', value: start_date });
				if (start_year_clim && end_year_clim) rows.push({ label: 'clim', value: `${start_year_clim}–${end_year_clim}` });
				if (init_days) rows.push({ label: 'init days', value: init_days });
				return rows;
			}
			case 'onset_rules': {
				const { wet_threshold, wet_spell, dry_spell } = configs.onset_rules;
				const rows: { label: string; value: string }[] = [];
				if (wet_threshold) rows.push({ label: 'wet ≥', value: `${wet_threshold}mm` });
				if (wet_spell) rows.push({ label: 'wet spell', value: `${wet_spell}d` });
				if (dry_spell) rows.push({ label: 'dry spell', value: `${dry_spell}d` });
				return rows;
			}
			case 'spatial_mask': {
				const { nc_mask } = configs.spatial_mask;
				if (!nc_mask) return [];
				return [{ label: 'mask', value: nc_mask.split('/').pop() ?? nc_mask }];
			}
			case 'benchmark': {
				const rows: { label: string; value: string }[] = [];
				rows.push({ label: 'parallel', value: configs.benchmark.parallel ? 'yes' : 'no' });
				rows.push({ label: 'forecast day', value: configs.benchmark.max_forecast_day_mode === 'default' ? 'model default' : configs.benchmark.max_forecast_day || 'custom' });
				if (configs.benchmark.probabilistic) rows.push({ label: 'probabilistic', value: 'yes' });
				return rows;
			}
			default:
				return [];
		}
	}

	$effect(() => {
		void configs;
		void schema;
		const issues = clientIssues;
		untrack(() => {
			let changed = false;
			for (const n of graphNodes) {
				const summary = buildConfigSummary(n.id);
				const nodeIssues = issues.filter((issue) => issue.node_id === n.id);
				const missingInputIds = nodeIssues
					.map((issue) => issue.port_id)
					.filter((portId): portId is string => Boolean(portId));
				const status = n.id === 'benchmark'
					? nodeIssues.length > 0
						? 'incomplete'
						: 'ready'
					: nodeIssues.length > 0
						? 'incomplete'
						: nodeConfigured(n.id)
							? 'configured'
							: n.data.status;
				if (
					JSON.stringify(n.data.configSummary) === JSON.stringify(summary) &&
					JSON.stringify(n.data.validationIssues) === JSON.stringify(nodeIssues.map((issue) => issue.message)) &&
					JSON.stringify(n.data.missingInputIds) === JSON.stringify(missingInputIds) &&
					n.data.status === status
				) {
					continue;
				}
				n.data = {
					...n.data,
					status,
					configSummary: summary,
					validationIssues: nodeIssues.map((issue) => issue.message),
					missingInputIds
				};
				changed = true;
			}
			if (changed) graphNodes = graphNodes;
		});
	});

	// ---------------------------------------------------------------------------
	// Submission
	// ---------------------------------------------------------------------------

	let submitting = $state(false);
	let submittedJobs = $state<Job[]>([]);
	let submitError = $state<string | null>(null);
	let validationErrors = $state<ClientValidationIssue[]>([]);
	const canRun = $derived(runBlockingIssues.length === 0 && !submitting);

	function buildWorkflowDocument(): WorkflowGraphDocument {
		const irNodes: IRNode[] = graphNodes.map((n) => {
			const base = { id: n.id, type: n.id === 'benchmark' ? 'benchmark' : n.id, label: n.data.label };
			switch (n.id) {
				case 'model_output_source':
					return { ...base, type: 'model_output_source', config: { model_names: configs.model_output_source.model_names } };
				case 'observation_dataset':
					return { ...base, type: 'observation_dataset', config: { dataset_id: configs.observation_dataset.dataset_id } };
				case 'region_event_definition':
					return { ...base, type: 'region_event_definition', config: { region: configs.region_event_definition.region, event_type: configs.region_event_definition.event_type } };
				case 'evaluation_window':
					return {
						...base, type: 'evaluation_window', config: {
							start_date: configs.evaluation_window.start_date || undefined,
							end_date: configs.evaluation_window.end_date || undefined,
							start_year_clim: configs.evaluation_window.start_year_clim ? Number(configs.evaluation_window.start_year_clim) : undefined,
							end_year_clim: configs.evaluation_window.end_year_clim ? Number(configs.evaluation_window.end_year_clim) : undefined,
							init_days: configs.evaluation_window.init_days || undefined
						}
					};
				case 'onset_rules':
					return {
						...base, type: 'onset_rules', config: {
							wet_threshold: configs.onset_rules.wet_threshold ? Number(configs.onset_rules.wet_threshold) : undefined,
							wet_spell: configs.onset_rules.wet_spell ? Number(configs.onset_rules.wet_spell) : undefined,
							dry_spell: configs.onset_rules.dry_spell ? Number(configs.onset_rules.dry_spell) : undefined,
							thresh_file: configs.onset_rules.thresh_file || undefined
						}
					};
				case 'spatial_mask':
					return { ...base, type: 'spatial_mask', config: { nc_mask: configs.spatial_mask.nc_mask || undefined } };
				case 'benchmark':
					return {
						...base, type: 'benchmark', config: {
							parallel: configs.benchmark.parallel,
							probabilistic: configs.benchmark.probabilistic,
							max_forecast_day: configs.benchmark.max_forecast_day_mode === 'custom' && configs.benchmark.max_forecast_day
								? Number(configs.benchmark.max_forecast_day)
								: undefined,
							members: configs.benchmark.probabilistic ? configs.benchmark.members || undefined : undefined
						}
					};
				default:
					return { ...base, config: {} };
			}
		});

		const irEdges: IREdge[] = graphEdges.map((e, i) => ({
			id: `e${i}`,
			source: { nodeId: String(e.source), portId: String(e.sourceHandle ?? '') },
			target: { nodeId: String(e.target), portId: String(e.targetHandle ?? '') }
		}));

		const maxForecastDay = configs.benchmark.max_forecast_day_mode === 'custom' && configs.benchmark.max_forecast_day
			? Number(configs.benchmark.max_forecast_day)
			: undefined;

		return {
			version: '0.1',
			graph: { nodes: irNodes, edges: irEdges },
			runConfig: {
				mode: 'interactive',
				parallel: configs.benchmark.parallel,
				probabilistic: configs.benchmark.probabilistic,
				maxForecastDay,
				members: configs.benchmark.probabilistic ? configs.benchmark.members || undefined : undefined
			},
			metadata: { name: 'Benchmark Run' }
		};
	}

	function buildIR(): WorkflowIR {
		const document = buildWorkflowDocument();
		return {
			version: document.version,
			graph: document.graph,
			run: { mode: document.runConfig.mode }
		};
	}

	async function handleRun() {
		const currentIssues = runBlockingIssues;
		if (currentIssues.length > 0) {
			validationErrors = currentIssues;
			selectedNodeId = currentIssues.find((issue) => issue.node_id)?.node_id ?? 'benchmark';
			return;
		}

		submitting = true;
		submitError = null;
		validationErrors = [];
		submittedJobs = [];

		const ir = buildIR();
		try {
			const result = await validateWorkflow(ir);
			if (!result.valid) {
				validationErrors = result.errors;
				submitting = false;
				return;
			}
			submittedJobs = await runWorkflow(ir);
		} catch (err) {
			submitError = err instanceof Error ? err.message : String(err);
		} finally {
			submitting = false;
		}
	}

	// ---------------------------------------------------------------------------
	// Grouped palette categories
	// ---------------------------------------------------------------------------

	const paletteGroups = $derived(() => {
		const groups: Record<string, PaletteEntry[]> = {};
		for (const entry of PALETTE) {
			(groups[entry.category] ??= []).push(entry);
		}
		return groups;
	});

	// ---------------------------------------------------------------------------
	// Selected node
	// ---------------------------------------------------------------------------

	const selectedNode = $derived(graphNodes.find((n) => n.id === selectedNodeId) ?? null);

	// ---------------------------------------------------------------------------
	// Validation error helpers
	// ---------------------------------------------------------------------------

	function errorsForNode(nodeId: string) {
		return allValidationIssues.filter((e) => e.node_id === nodeId);
	}

	const allValidationIssues = $derived.by(() => {
		const seen = new Set<string>();
		return [...clientIssues, ...validationErrors].filter((issue) => {
			const key = `${issue.node_id ?? ''}:${issue.field ?? ''}:${issue.port_id ?? ''}:${issue.message}`;
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	});

	const globalErrors = $derived(allValidationIssues.filter((e) => !e.node_id));

	const nodeTypes = { workflow: WorkflowNode };
</script>

<svelte:head>
	<title>Benchmark Workflow | AI Almanac</title>
</svelte:head>

<main class="workflow-page">
	<div class="workflow-shell">
		<header class="workflow-header">
			<div>
				<p class="eyebrow">Workflow</p>
				<h1>Benchmark Run</h1>
				<p class="run-state" class:ready={runBlockingIssues.length === 0}>
					{#if runBlockingIssues.length === 0}
						Ready to submit
					{:else}
						{runBlockingIssues.length} blocking issue{runBlockingIssues.length === 1 ? '' : 's'}
					{/if}
				</p>
			</div>
			<div class="header-actions">
				{#if submittedJobs.length > 0}
					<a href="/benchmarks" class="link-action">View results →</a>
				{/if}
				<button type="button" class="btn-run" onclick={handleRun} disabled={!canRun}>
					{submitting ? 'Submitting…' : 'Run Workflow'}
				</button>
			</div>
		</header>

		{#if submitError}
			<div class="alert alert-error">{submitError}</div>
		{/if}

		{#if submittedJobs.length > 0}
			<div class="alert alert-success">
				Submitted {submittedJobs.length} job{submittedJobs.length !== 1 ? 's' : ''} — run ID
				<code>{submittedJobs[0].run_id}</code>.
				<a href="/benchmarks">View in benchmarks →</a>
			</div>
		{/if}

		{#if globalErrors.length > 0}
			<div class="alert alert-error">
				{#each globalErrors as err}
					<div>{err.message}</div>
				{/each}
			</div>
		{/if}

		<div class="workspace">
			<!-- Left: node palette -->
			<aside class="palette">
				<p class="panel-label">Node palette</p>
				<p class="palette-hint">Add nodes to the canvas, then draw connections between handles.</p>

				{#each Object.entries(paletteGroups()) as [category, entries]}
					<div class="palette-group">
						<p class="group-label">{category}</p>
						{#each entries as entry}
							{@const inGraph = nodeTypeInGraph(entry.type)}
							{@const hasError = inGraph && errorsForNode(entry.type).length > 0}
							<div class="palette-item" class:in-graph={inGraph} class:has-error={hasError}>
								<div class="palette-item-info">
									<span class="palette-item-name">{entry.label}</span>
									{#if entry.required}
										<span class="badge-required">required</span>
									{:else}
										<span class="badge-optional">optional</span>
									{/if}
								</div>
								<p class="palette-item-desc">{entry.description}</p>
								{#if hasError}
									{#each errorsForNode(entry.type) as err}
										<p class="palette-item-error">{err.message}</p>
									{/each}
								{/if}
								{#if inGraph}
									<div class="palette-item-actions">
										<button
											type="button"
											class="btn-configure"
											onclick={() => (selectedNodeId = entry.type)}
										>
											Configure
										</button>
										{#if entry.type !== 'benchmark'}
											<button
												type="button"
												class="btn-remove"
												onclick={() => removeNodeFromGraph(entry.type)}
											>
												Remove
											</button>
										{/if}
									</div>
								{:else}
									<button
										type="button"
										class="btn-add"
										onclick={() => addNodeToGraph(entry)}
									>
										{entry.addAction}
									</button>
								{/if}
							</div>
						{/each}
					</div>
				{/each}

				<div class="palette-group">
					<p class="group-label">Execution</p>
					<div class="palette-item in-graph always-present">
						<div class="palette-item-info">
							<span class="palette-item-name">Benchmark Run</span>
							<span class="badge-required">always present</span>
						</div>
						<p class="palette-item-desc">Compile config and submit benchmark jobs.</p>
						<button
							type="button"
							class="btn-configure"
							onclick={() => (selectedNodeId = 'benchmark')}
						>
							Configure
						</button>
					</div>
				</div>
			</aside>

			<!-- Center: canvas -->
			<section class="canvas-panel" aria-label="Workflow graph">
				<SvelteFlow
					bind:nodes={graphNodes}
					bind:edges={graphEdges}
					{nodeTypes}
					colorMode="dark"
					fitView
					fitViewOptions={{ padding: 0.22 }}
					minZoom={0.35}
					maxZoom={1.5}
					ondelete={onDelete}
					onbeforeconnect={onBeforeConnect}
					{isValidConnection}
					onnodeclick={({ node }) => (selectedNodeId = node.id)}
					onpaneclick={() => (selectedNodeId = null)}
				>
					<Background
						variant={BackgroundVariant.Lines}
						gap={[28, 28]}
						patternColor="rgba(130, 168, 200, 0.16)"
						bgColor="rgba(7, 18, 32, 0.78)"
					/>
					<Controls />
					<MiniMap pannable zoomable nodeStrokeWidth={3} />
				</SvelteFlow>
			</section>

			<!-- Right: inspector -->
			<aside class="inspector">
				{#if selectedNode}
					<p class="panel-label">Configure</p>
					<h2>{selectedNode.data.label}</h2>
					<p class="summary">{selectedNode.data.description}</p>

					{#if errorsForNode(selectedNodeId ?? '').length > 0}
						<div class="node-errors">
							{#each errorsForNode(selectedNodeId ?? '') as err}
								<p>{err.message}</p>
							{/each}
						</div>
					{/if}

					<!-- Model source -->
					{#if selectedNodeId === 'model_output_source'}
						<div class="field-group">
							<p class="field-group-label">Models <span class="req">required</span></p>
							{#if schemaError}
								<p class="error-text">{schemaError}</p>
							{:else if !schema}
								<p class="muted">Loading…</p>
							{:else if !activeRegion()}
								<p class="optional-note">Select a region in the Region + Event node before choosing model predictions.</p>
							{:else if filteredModels().length === 0}
								<p class="muted">
									No models available for region "{activeRegion()}".
								</p>
							{:else}
								<div class="model-grid">
									{#each filteredModels() as model (`${model.region}:${model.id}`)}
										<button
											type="button"
											class="model-chip"
											class:selected={configs.model_output_source.model_names.includes(model.id)}
											onclick={() => toggleModel(model.id)}
										>
											<span class="model-name">{model.display_name}</span>
											<span class="model-region">{model.region}</span>
										</button>
									{/each}
								</div>
								{#if activeRegion()}
									<p class="hint">Showing models for region <strong>{activeRegion()}</strong>.</p>
								{/if}
							{/if}
						</div>

					<!-- Observation dataset -->
					{:else if selectedNodeId === 'observation_dataset'}
						<div class="field-group">
							<label>
								<span>Dataset <span class="req">required</span></span>
								{#if !schema}
									<select disabled><option>Loading…</option></select>
								{:else if !activeRegion()}
									<select disabled><option>Select a region first</option></select>
								{:else}
									<select bind:value={configs.observation_dataset.dataset_id}>
										<option value="">— select —</option>
										{#each filteredDatasets() as ds}
											<option value={ds.id}>{ds.name}</option>
										{/each}
									</select>
								{/if}
							</label>
							{#if !activeRegion()}
								<p class="hint">The Region + Event node controls which datasets are valid for this run.</p>
							{:else if filteredDatasets().length === 0}
								<p class="muted">No observation datasets available for region "{activeRegion()}".</p>
							{:else if configs.observation_dataset.dataset_id && schema}
								{@const ds = schema.availableDatasets.find((d) => d.id === configs.observation_dataset.dataset_id)}
								{#if ds?.region}
									<p class="hint">This dataset is for region <strong>{ds.region}</strong>.</p>
								{/if}
							{/if}
						</div>

					<!-- Region + Event -->
					{:else if selectedNodeId === 'region_event_definition'}
						<div class="field-group">
							<label>
								<span>Region <span class="req">required</span></span>
								<select bind:value={configs.region_event_definition.region}>
									<option value="">— select —</option>
									<option value="india">India</option>
									<option value="ethiopia">Ethiopia</option>
									<option value="test">Test</option>
								</select>
							</label>
							<label>
								<span>Event type <span class="req">required</span></span>
								<select bind:value={configs.region_event_definition.event_type}>
									<option value="">— select —</option>
									<option value="monsoon_onset">Monsoon Onset</option>
									<option value="monsoon_cessation">Monsoon Cessation</option>
								</select>
							</label>
						</div>

					<!-- Evaluation window -->
					{:else if selectedNodeId === 'evaluation_window'}
						<div class="field-group">
							<p class="optional-note">Optional — leave blank to use per-model defaults from the model registry.</p>
							<label>
								<span>Start date</span>
								<input type="date" bind:value={configs.evaluation_window.start_date} />
							</label>
							<label>
								<span>End date</span>
								<input type="date" bind:value={configs.evaluation_window.end_date} />
							</label>
							<label>
								<span>Climatology start year</span>
								<input type="number" bind:value={configs.evaluation_window.start_year_clim} placeholder="e.g. 1991" />
							</label>
							<label>
								<span>Climatology end year</span>
								<input type="number" bind:value={configs.evaluation_window.end_year_clim} placeholder="e.g. 2020" />
							</label>
							<label>
								<span>Init days</span>
								<input type="text" bind:value={configs.evaluation_window.init_days} placeholder="e.g. 0,3" />
							</label>
						</div>

					<!-- Onset rules -->
					{:else if selectedNodeId === 'onset_rules'}
						<div class="field-group">
							<p class="optional-note">Optional — leave blank to use ROMP defaults (wet ≥20mm, 3-day wet spell, 7-day dry spell).</p>
							<label>
								<span>Wet threshold (mm)</span>
								<input type="number" bind:value={configs.onset_rules.wet_threshold} placeholder="20" />
							</label>
							<label>
								<span>Wet spell (days)</span>
								<input type="number" bind:value={configs.onset_rules.wet_spell} placeholder="3" />
							</label>
							<label>
								<span>Dry spell (days)</span>
								<input type="number" bind:value={configs.onset_rules.dry_spell} placeholder="7" />
							</label>
							<label>
								<span>Threshold file</span>
								<input type="text" bind:value={configs.onset_rules.thresh_file} placeholder="/path/to/threshold.nc" />
							</label>
						</div>

					<!-- Spatial mask -->
					{:else if selectedNodeId === 'spatial_mask'}
						<div class="field-group">
							<p class="optional-note">Optional — provide a NetCDF mask to exclude grid cells from metric calculations.</p>
							<label>
								<span>NetCDF mask file</span>
								<input type="text" bind:value={configs.spatial_mask.nc_mask} placeholder="/path/to/mask.nc" />
							</label>
						</div>

					<!-- Benchmark execution -->
					{:else if selectedNodeId === 'benchmark'}
						<div class="field-group">
							<p class="optional-note">Execution options. Defaults work for most benchmarks.</p>
							<label class="checkbox-field">
								<span>Parallel execution</span>
								<input type="checkbox" bind:checked={configs.benchmark.parallel} />
							</label>
							<label class="checkbox-field">
								<span>Probabilistic</span>
								<input type="checkbox" bind:checked={configs.benchmark.probabilistic} />
							</label>
							<div class="forecast-day-control">
								<span>Max forecast day</span>
								<div class="segmented-control" role="group" aria-label="Max forecast day mode">
									<button
										type="button"
										class:active={configs.benchmark.max_forecast_day_mode === 'default'}
										onclick={() => (configs.benchmark.max_forecast_day_mode = 'default')}
									>
										Model default
									</button>
									<button
										type="button"
										class:active={configs.benchmark.max_forecast_day_mode === 'custom'}
										onclick={() => (configs.benchmark.max_forecast_day_mode = 'custom')}
									>
										Custom
									</button>
								</div>
							</div>
							{#if configs.benchmark.max_forecast_day_mode === 'custom'}
								<label>
									<span>Custom max forecast day</span>
									<input type="number" min="1" bind:value={configs.benchmark.max_forecast_day} placeholder="e.g. 30" />
								</label>
							{/if}
							{#if configs.benchmark.probabilistic}
								<label>
									<span>Ensemble members</span>
									<input type="text" bind:value={configs.benchmark.members} placeholder="All" />
								</label>
							{/if}
						</div>
					{/if}

					<!-- Port reference -->
					{#if selectedNode.data.inputs?.length || selectedNode.data.outputs?.length}
						<div class="ports-panel">
							{#if selectedNode.data.inputs?.length}
								<div>
									<p class="panel-label">Inputs</p>
									{#each selectedNode.data.inputs as p}
										{@const connectedEdge = inputConnection(selectedNode.id, p.id)}
										<span
											class:connected={Boolean(connectedEdge)}
											class:missing={Boolean(p.required && !connectedEdge)}
											style={`--port-color: ${portColors[p.type]}`}
										>
											{p.label}{p.required ? ' *' : ''}
											<small>{connectedEdge ? `from ${connectedEdge.source}` : p.required ? 'missing' : 'optional'}</small>
										</span>
									{/each}
								</div>
							{/if}
							{#if selectedNode.data.outputs?.length}
								<div>
									<p class="panel-label">Outputs</p>
									{#each selectedNode.data.outputs as p}
										<span style={`--port-color: ${portColors[p.type]}`}>{p.label}</span>
									{/each}
								</div>
							{/if}
						</div>
					{/if}

				{:else}
					<div class="inspector-empty">
						<p class="panel-label">Workflow status</p>
						<p>{runBlockingIssues.length === 0 ? 'Ready to submit.' : 'Resolve the blocking issues before running this benchmark.'}</p>
						<div class="canvas-tips">
							<p class="tip-label">Blocking issues</p>
							{#if runBlockingIssues.length > 0}
								<ul>
									{#each runBlockingIssues as issue}
										<li>{issue.message}</li>
									{/each}
								</ul>
							{:else}
								<p class="muted">No blocking issues.</p>
							{/if}
						</div>
					</div>
				{/if}
			</aside>
		</div>
	</div>
</main>

<style>
	.workflow-page {
		min-height: calc(100vh - 3.5rem);
		padding: clamp(1rem, 2vw, 1.6rem);
	}

	.workflow-shell {
		max-width: 92rem;
		margin: 0 auto;
	}

	.workflow-header {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 1rem;
	}

	.eyebrow,
	.panel-label {
		margin: 0 0 0.28rem;
		font-family: var(--font-mono);
		font-size: 0.68rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--color-accent);
	}

	h1,
	h2 {
		margin: 0;
		font-family: var(--font-display);
		font-weight: 400;
	}

	h1 {
		font-size: clamp(1.8rem, 3vw, 3.5rem);
		line-height: 0.95;
	}

	.run-state {
		margin: 0.45rem 0 0;
		color: #fca5a5;
		font-size: 0.82rem;
		font-weight: 700;
	}

	.run-state.ready {
		color: #86efac;
	}

	h2 {
		font-size: 1.3rem;
		line-height: 1.1;
	}

	.header-actions {
		display: flex;
		align-items: center;
		gap: 0.8rem;
	}

	.btn-run {
		border: 1px solid var(--color-accent);
		border-radius: 0.45rem;
		background: var(--color-accent-glow);
		color: var(--color-text);
		font-family: var(--font-body);
		font-size: 0.85rem;
		font-weight: 700;
		padding: 0.6rem 1.25rem;
		cursor: pointer;
		transition: background-color 0.15s ease;
	}

	.btn-run:hover:not(:disabled) {
		background: rgba(212, 147, 63, 0.22);
	}

	.btn-run:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.link-action {
		color: var(--color-accent);
		font-size: 0.82rem;
		text-decoration: none;
	}

	.link-action:hover {
		text-decoration: underline;
	}

	.alert {
		margin-bottom: 1rem;
		border-radius: 0.45rem;
		padding: 0.75rem 1rem;
		font-size: 0.84rem;
		line-height: 1.5;
	}

	.alert-error {
		border: 1px solid rgba(220, 80, 80, 0.4);
		background: rgba(220, 80, 80, 0.1);
		color: #f08080;
	}

	.alert-success {
		border: 1px solid rgba(80, 200, 120, 0.4);
		background: rgba(80, 200, 120, 0.08);
		color: #7eebb0;
	}

	.alert-success code {
		font-family: var(--font-mono);
		font-size: 0.8em;
	}

	.alert-success a {
		color: var(--color-accent);
	}

	.workspace {
		display: grid;
		grid-template-columns: minmax(14rem, 0.85fr) minmax(26rem, 2.2fr) minmax(14rem, 0.9fr);
		gap: 1rem;
		align-items: stretch;
	}

	/* ---- Palette ---- */

	.palette {
		border: 1px solid rgba(130, 168, 200, 0.2);
		border-radius: 0.65rem;
		background: rgba(18, 42, 67, 0.72);
		box-shadow: 0 1rem 3rem rgba(2, 10, 20, 0.18);
		padding: 1rem;
		min-height: 38rem;
		overflow-y: auto;
	}

	.palette-hint {
		margin: 0 0 1.2rem;
		color: var(--color-text-dim);
		font-size: 0.76rem;
		line-height: 1.5;
	}

	.palette-group {
		margin-bottom: 1.4rem;
	}

	.group-label {
		margin: 0 0 0.5rem;
		font-family: var(--font-mono);
		font-size: 0.62rem;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--color-text-dim);
	}

	.palette-item {
		border: 1px solid rgba(130, 168, 200, 0.14);
		border-radius: 0.5rem;
		background: rgba(255, 255, 255, 0.025);
		padding: 0.65rem 0.75rem;
		margin-bottom: 0.5rem;
		transition: border-color 0.15s ease;
	}

	.palette-item.in-graph {
		border-color: rgba(130, 168, 200, 0.28);
		background: rgba(255, 255, 255, 0.045);
	}

	.palette-item.always-present {
		border-color: rgba(212, 147, 63, 0.28);
		background: rgba(212, 147, 63, 0.05);
	}

	.palette-item.has-error {
		border-color: rgba(220, 80, 80, 0.45);
		background: rgba(220, 80, 80, 0.06);
	}

	.palette-item-info {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		margin-bottom: 0.3rem;
	}

	.palette-item-name {
		font-size: 0.83rem;
		font-weight: 600;
		color: var(--color-text);
		line-height: 1.2;
	}

	.badge-required,
	.badge-optional {
		font-family: var(--font-mono);
		font-size: 0.58rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		border-radius: 999rem;
		padding: 0.1rem 0.38rem;
		white-space: nowrap;
	}

	.badge-required {
		border: 1px solid rgba(212, 147, 63, 0.45);
		color: var(--color-accent);
		background: rgba(212, 147, 63, 0.1);
	}

	.badge-optional {
		border: 1px solid rgba(130, 168, 200, 0.2);
		color: var(--color-text-dim);
		background: transparent;
	}

	.palette-item-desc {
		margin: 0 0 0.55rem;
		color: var(--color-text-dim);
		font-size: 0.74rem;
		line-height: 1.45;
	}

	.palette-item-error {
		margin: 0 0 0.4rem;
		color: #f08080;
		font-size: 0.73rem;
		line-height: 1.4;
	}

	.palette-item-actions {
		display: flex;
		gap: 0.4rem;
	}

	.btn-add,
	.btn-configure,
	.btn-remove {
		border-radius: 0.35rem;
		font-family: var(--font-body);
		font-size: 0.74rem;
		font-weight: 600;
		padding: 0.3rem 0.65rem;
		cursor: pointer;
		transition: background-color 0.15s ease, border-color 0.15s ease;
	}

	.btn-add {
		width: 100%;
		border: 1px solid rgba(130, 168, 200, 0.28);
		background: rgba(130, 168, 200, 0.07);
		color: var(--color-text-muted);
	}

	.btn-add:hover {
		border-color: var(--color-accent-border);
		background: var(--color-accent-glow);
		color: var(--color-text);
	}

	.btn-configure {
		border: 1px solid rgba(130, 168, 200, 0.28);
		background: rgba(130, 168, 200, 0.07);
		color: var(--color-text-muted);
		flex: 1;
	}

	.btn-configure:hover {
		border-color: var(--color-accent-border);
		background: var(--color-accent-glow);
		color: var(--color-text);
	}

	.btn-remove {
		border: 1px solid rgba(220, 80, 80, 0.28);
		background: transparent;
		color: rgba(220, 80, 80, 0.7);
	}

	.btn-remove:hover {
		background: rgba(220, 80, 80, 0.1);
		color: #f08080;
	}

	/* ---- Canvas ---- */

	.canvas-panel {
		position: relative;
		overflow: hidden;
		min-height: 38rem;
		border: 1px solid rgba(130, 168, 200, 0.2);
		border-radius: 0.65rem;
		background: rgba(18, 42, 67, 0.72);
		box-shadow: 0 1rem 3rem rgba(2, 10, 20, 0.18);
	}

	.canvas-panel :global(.svelte-flow__attribution) {
		display: none;
	}

	.canvas-panel :global(.svelte-flow__controls-button) {
		border-color: rgba(130, 168, 200, 0.2);
		background: rgba(12, 28, 46, 0.95);
		color: var(--color-text);
	}

	.canvas-panel :global(.svelte-flow__minimap) {
		border: 1px solid rgba(130, 168, 200, 0.16);
		border-radius: 0.45rem;
		overflow: hidden;
		background: rgba(10, 23, 39, 0.94);
	}

	/* ---- Inspector ---- */

	.inspector {
		border: 1px solid rgba(130, 168, 200, 0.2);
		border-radius: 0.65rem;
		background: rgba(18, 42, 67, 0.72);
		box-shadow: 0 1rem 3rem rgba(2, 10, 20, 0.18);
		padding: 1rem;
		min-height: 38rem;
		overflow-y: auto;
	}

	.summary {
		margin: 0.5rem 0 1rem;
		color: var(--color-text-muted);
		font-size: 0.83rem;
		line-height: 1.5;
	}

	.node-errors {
		margin-bottom: 0.8rem;
		border: 1px solid rgba(220, 80, 80, 0.35);
		border-radius: 0.4rem;
		background: rgba(220, 80, 80, 0.08);
		padding: 0.55rem 0.7rem;
		color: #f08080;
		font-size: 0.78rem;
		line-height: 1.5;
	}

	.node-errors p {
		margin: 0;
	}

	.field-group {
		display: grid;
		gap: 0.7rem;
		margin-bottom: 1.2rem;
	}

	.field-group label {
		display: grid;
		gap: 0.3rem;
		color: var(--color-text-muted);
		font-size: 0.77rem;
		font-weight: 600;
	}

	.field-group label.checkbox-field {
		grid-template-columns: 1fr auto;
		align-items: center;
		border: 1px solid rgba(130, 168, 200, 0.14);
		border-radius: 0.36rem;
		background: rgba(255, 255, 255, 0.03);
		padding: 0.5rem 0.6rem;
	}

	.forecast-day-control {
		display: grid;
		gap: 0.35rem;
		color: var(--color-text-muted);
		font-size: 0.77rem;
		font-weight: 600;
	}

	.segmented-control {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		border: 1px solid rgba(130, 168, 200, 0.2);
		border-radius: 0.36rem;
		background: rgba(7, 18, 32, 0.64);
		padding: 0.18rem;
	}

	.segmented-control button {
		border: 0;
		border-radius: 0.24rem;
		background: transparent;
		color: var(--color-text-muted);
		cursor: pointer;
		font-family: var(--font-body);
		font-size: 0.75rem;
		font-weight: 700;
		padding: 0.38rem 0.45rem;
	}

	.segmented-control button.active {
		background: rgba(212, 147, 63, 0.18);
		color: var(--color-text);
	}

	.field-group input:not([type='checkbox']),
	.field-group select {
		width: 100%;
		border: 1px solid rgba(130, 168, 200, 0.22);
		border-radius: 0.36rem;
		background: rgba(7, 18, 32, 0.84);
		color: var(--color-text);
		font-family: var(--font-body);
		font-size: 0.82rem;
		outline: none;
		padding: 0.46rem 0.54rem;
		transition: border-color 0.15s ease, box-shadow 0.15s ease;
		box-sizing: border-box;
	}

	.field-group input:not([type='checkbox']):focus,
	.field-group select:focus {
		border-color: var(--color-accent);
		box-shadow: 0 0 0 0.18rem var(--color-accent-light);
	}

	.field-group input[type='checkbox'] {
		width: 1rem;
		height: 1rem;
		accent-color: var(--color-accent);
	}

	.req {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--color-accent);
		font-weight: 400;
	}

	.optional-note {
		margin: 0;
		border: 1px solid rgba(130, 168, 200, 0.14);
		border-radius: 0.36rem;
		background: rgba(255, 255, 255, 0.025);
		color: var(--color-text-dim);
		font-size: 0.75rem;
		line-height: 1.5;
		padding: 0.5rem 0.65rem;
	}

	.hint {
		margin: 0;
		color: var(--color-text-dim);
		font-size: 0.74rem;
		line-height: 1.4;
	}

	.hint strong {
		color: var(--color-text-muted);
	}

	.model-grid {
		display: grid;
		gap: 0.4rem;
	}

	.model-chip {
		display: flex;
		align-items: center;
		justify-content: space-between;
		border: 1px solid rgba(130, 168, 200, 0.18);
		border-radius: 0.4rem;
		background: rgba(255, 255, 255, 0.03);
		color: var(--color-text-muted);
		cursor: pointer;
		font-family: var(--font-body);
		padding: 0.48rem 0.65rem;
		text-align: left;
		transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
	}

	.model-chip:hover {
		border-color: var(--color-accent-border);
		background: var(--color-accent-glow);
		color: var(--color-text);
	}

	.model-chip.selected {
		border-color: var(--color-accent);
		background: rgba(212, 147, 63, 0.14);
		color: var(--color-text);
	}

	.model-name {
		font-size: 0.82rem;
		font-weight: 600;
	}

	.model-region {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		text-transform: uppercase;
		color: var(--color-text-dim);
	}

	.ports-panel {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.7rem;
		border-top: 1px solid rgba(130, 168, 200, 0.14);
		padding-top: 1rem;
		margin-top: 0.2rem;
	}

	.ports-panel > div {
		display: grid;
		gap: 0.35rem;
		align-content: start;
	}

	.ports-panel span {
		display: grid;
		gap: 0.14rem;
		border: 1px solid rgba(130, 168, 200, 0.16);
		border-left: 0.28rem solid var(--port-color);
		border-radius: 0.28rem;
		background: rgba(255, 255, 255, 0.035);
		color: var(--color-text-muted);
		font-family: var(--font-mono);
		font-size: 0.62rem;
		line-height: 1.2;
		padding: 0.2rem 0.38rem;
		text-transform: uppercase;
	}

	.ports-panel span.connected {
		border-color: rgba(52, 211, 153, 0.28);
		background: rgba(52, 211, 153, 0.06);
	}

	.ports-panel span.missing {
		border-color: rgba(220, 80, 80, 0.35);
		background: rgba(220, 80, 80, 0.07);
		color: #fca5a5;
	}

	.ports-panel small {
		color: var(--color-text-dim);
		font-family: var(--font-body);
		font-size: 0.66rem;
		font-weight: 600;
		line-height: 1.2;
		text-transform: none;
	}

	.inspector-empty {
		height: 100%;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}

	.inspector-empty > p {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.84rem;
		line-height: 1.5;
	}

	.canvas-tips {
		margin-top: 0.4rem;
		border: 1px solid rgba(130, 168, 200, 0.12);
		border-radius: 0.45rem;
		background: rgba(255, 255, 255, 0.025);
		padding: 0.75rem;
	}

	.tip-label {
		margin: 0 0 0.5rem;
		font-family: var(--font-mono);
		font-size: 0.62rem;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--color-text-dim);
	}

	.canvas-tips ul {
		margin: 0;
		padding-left: 1.1rem;
		display: grid;
		gap: 0.38rem;
	}

	.canvas-tips li {
		color: var(--color-text-dim);
		font-size: 0.78rem;
		line-height: 1.4;
	}

	.muted {
		color: var(--color-text-dim);
		font-size: 0.8rem;
		margin: 0;
	}

	.error-text {
		color: #f08080;
		font-size: 0.8rem;
		margin: 0;
	}

	@media (max-width: 62rem) {
		.workspace {
			grid-template-columns: 1fr;
		}

		.canvas-panel {
			min-height: 32rem;
		}

		.palette,
		.inspector {
			min-height: auto;
		}
	}
</style>
