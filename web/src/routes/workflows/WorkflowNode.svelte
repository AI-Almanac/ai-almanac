<script lang="ts">
	import { Handle, Position } from '@xyflow/svelte';

	export type WorkflowNodeKind =
		| 'catalog'
		| 'blend'
		| 'benchmark'
		| 'forecast'
		| 'preprocess'
		| 'postprocess'
		| 'artifact';

	export type WorkflowNodeData = {
		label: string;
		kind: WorkflowNodeKind;
		phase: string;
		description?: string;
		status: 'draft' | 'incomplete' | 'configured' | 'ready' | 'running' | 'complete';
		chips?: string[];
		parameters?: string[];
		configFields?: WorkflowConfigField[];
		config?: Record<string, string | number | boolean>;
		configSummary?: { label: string; value: string }[];
		validationIssues?: string[];
		missingInputIds?: string[];
		inputs?: WorkflowPort[];
		outputs?: WorkflowPort[];
	};

	export type WorkflowPortType =
		| 'model-output'
		| 'observation-dataset'
		| 'region-event-config'
		| 'evaluation-window'
		| 'onset-rules'
		| 'spatial-mask'
		| 'benchmark-results'
		| 'forecast-products'
		| 'control';

	export type WorkflowPort = {
		id: string;
		label: string;
		type: WorkflowPortType;
		required?: boolean;
	};

	export type WorkflowConfigField = {
		name: string;
		label: string;
		type: 'text' | 'number' | 'date' | 'checkbox' | 'select';
		placeholder?: string;
		help?: string;
		options?: { label: string; value: string }[];
	};

	let { data, selected = false }: { data: WorkflowNodeData; selected?: boolean } = $props();

	const statusLabel: Record<WorkflowNodeData['status'], string> = {
		draft: 'Draft',
		incomplete: 'Incomplete',
		configured: 'Configured',
		ready: 'Ready',
		running: 'Running',
		complete: 'Complete'
	};

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

	// Pair inputs and outputs into rows. Unpaired ports get null on the missing side.
	const portRows = $derived(
		Array.from(
			{ length: Math.max(data.inputs?.length ?? 0, data.outputs?.length ?? 0) },
			(_, i) => ({ input: data.inputs?.[i] ?? null, output: data.outputs?.[i] ?? null })
		)
	);

	const visibleChips = $derived((data.chips ?? []).filter((c) => c !== 'optional'));
	const isOptional = $derived((data.chips ?? []).includes('optional'));
	const issueCount = $derived(data.validationIssues?.length ?? 0);
</script>

<div class="workflow-node {data.kind}" class:selected class:optional={isOptional} class:invalid={issueCount > 0}>
	<div class="node-header">
		<span class="phase">{data.phase}</span>
		<span class="status {data.status}">{statusLabel[data.status]}</span>
	</div>

	<h3>{data.label}</h3>

	{#if visibleChips.length}
		<div class="chip-row">
			{#each visibleChips as chip}
				<span>{chip}</span>
			{/each}
		</div>
	{/if}

	<div class="config-summary">
		{#if issueCount > 0}
			<span class="issue-chip">{issueCount} issue{issueCount === 1 ? '' : 's'}</span>
		{/if}
		{#if data.configSummary?.length}
			{#each data.configSummary as item}
				<span class="summary-chip">
					<span class="chip-key">{item.label}</span>
					<span class="chip-val" title={item.value}>{item.value}</span>
				</span>
			{/each}
		{:else if data.status !== 'ready' && issueCount === 0}
			<span class="unconfigured">not configured</span>
		{/if}
	</div>

	{#if portRows.length}
		<div class="port-section">
			{#each portRows as row}
				<div class="port-row">
					<!-- Input side -->
					<div class="port-cell input-cell">
						{#if row.input}
							{@const isMissing = data.missingInputIds?.includes(row.input.id)}
							<Handle
								id={row.input.id}
								type="target"
								position={Position.Left}
								class={`port-handle ${isMissing ? 'missing' : ''}`}
								style={`background: ${portColors[row.input.type]};`}
							/>
							<span class="port-name" class:missing={isMissing} style={`color: ${portColors[row.input.type]}`}>
								{row.input.label}{row.input.required ? ' *' : ''}
							</span>
						{/if}
					</div>
					<!-- Output side -->
					<div class="port-cell output-cell">
						{#if row.output}
							<span class="port-name" style={`color: ${portColors[row.output.type]}`}>
								{row.output.label}
							</span>
							<Handle
								id={row.output.id}
								type="source"
								position={Position.Right}
								class="port-handle"
								style={`background: ${portColors[row.output.type]};`}
							/>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	/* ---- Node container ---- */
	.workflow-node {
		width: clamp(13rem, 15vw, 17rem);
		border: 1px solid rgba(130, 168, 200, 0.24);
		border-left: 0.32rem solid var(--node-color, var(--color-accent));
		border-radius: 0.5rem;
		background:
			linear-gradient(145deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.015)),
			rgba(13, 29, 48, 0.95);
		box-shadow: 0 1rem 2.5rem rgba(4, 12, 24, 0.28);
		color: var(--color-text);
		padding: 0.75rem 0.9rem 0.65rem;
		overflow: visible; /* Allow handles to extend outside node bounds */
		transition:
			border-color 0.15s ease,
			box-shadow 0.15s ease;
	}

	.workflow-node.selected {
		border-color: rgba(212, 147, 63, 0.78);
		box-shadow:
			0 1rem 2.5rem rgba(4, 12, 24, 0.38),
			0 0 0 0.14rem rgba(212, 147, 63, 0.18);
	}

	.workflow-node.invalid {
		border-color: rgba(220, 80, 80, 0.58);
		box-shadow:
			0 1rem 2.5rem rgba(4, 12, 24, 0.38),
			0 0 0 0.12rem rgba(220, 80, 80, 0.12);
	}

	.workflow-node.optional {
		border-style: dashed;
		border-left-style: solid;
	}

	/* ---- Kind colors ---- */
	.workflow-node.blend        { --node-color: #79c2ff; }
	.workflow-node.benchmark    { --node-color: #d4933f; }
	.workflow-node.forecast     { --node-color: #70d6a6; }
	.workflow-node.preprocess   { --node-color: #c8a8ff; }
	.workflow-node.postprocess  { --node-color: #f4b8a8; }
	.workflow-node.catalog,
	.workflow-node.artifact     { --node-color: #82a8c8; }

	/* ---- Header ---- */
	.node-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 0.25rem;
	}

	.phase {
		font-family: var(--font-mono);
		font-size: 0.62rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--node-color, var(--color-accent));
	}

	.status {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border: 1px solid rgba(130, 168, 200, 0.2);
		border-radius: 999rem;
		background: rgba(255, 255, 255, 0.045);
		color: var(--color-text-muted);
		padding: 0.1rem 0.4rem;
	}

	.status.complete { border-color: rgba(52, 211, 153, 0.36); color: #86efac; }
	.status.running  { border-color: rgba(251, 191, 36, 0.36);  color: #fde68a; }
	.status.ready,
	.status.configured { border-color: rgba(52, 211, 153, 0.3); color: #86efac; }
	.status.incomplete { border-color: rgba(220, 80, 80, 0.34); color: #fca5a5; }

	/* ---- Title ---- */
	h3 {
		margin: 0 0 0.3rem;
		font-family: var(--font-display);
		font-size: 1.2rem;
		font-weight: 400;
		line-height: 1.1;
		letter-spacing: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* ---- Chips ---- */
	.chip-row {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
		margin-bottom: 0.2rem;
	}

	.chip-row span {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border: 1px solid rgba(130, 168, 200, 0.2);
		border-radius: 999rem;
		background: rgba(255, 255, 255, 0.045);
		color: var(--color-text-muted);
		padding: 0.1rem 0.38rem;
	}

	/* ---- Config summary ---- */
	.config-summary {
		display: flex;
		flex-wrap: wrap;
		gap: 0.2rem;
		margin-top: 0.4rem;
	}

	.summary-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.22rem;
		border: 1px solid rgba(130, 168, 200, 0.14);
		border-radius: 0.22rem;
		background: rgba(255, 255, 255, 0.035);
		padding: 0.1rem 0.3rem;
		max-width: 100%;
		overflow: hidden;
	}

	.issue-chip {
		border: 1px solid rgba(220, 80, 80, 0.28);
		border-radius: 0.22rem;
		background: rgba(220, 80, 80, 0.09);
		color: #fca5a5;
		font-family: var(--font-mono);
		font-size: 0.58rem;
		letter-spacing: 0.04em;
		padding: 0.1rem 0.3rem;
		text-transform: uppercase;
	}

	.chip-key {
		font-family: var(--font-mono);
		font-size: 0.56rem;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--color-text-dim);
		white-space: nowrap;
		flex-shrink: 0;
	}

	.chip-val {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		color: var(--color-text-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.unconfigured {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		color: var(--color-text-dim);
		font-style: italic;
	}

	/* ---- Port section ---- */
	.port-section {
		/*
		 * Extend to node edges by canceling node padding, so handles can sit
		 * exactly at the border. Each port-row re-adds padding for label text.
		 */
		margin: 0.55rem -0.9rem 0;
		border-top: 1px solid rgba(130, 168, 200, 0.14);
		padding-top: 0.1rem;
	}

	.port-row {
		position: relative; /* Handles are absolute within this row */
		display: grid;
		grid-template-columns: 1fr 1fr;
		min-height: 1.5rem;
	}

	.port-cell {
		display: flex;
		align-items: center;
		padding: 0.2rem 0.7rem;
		gap: 0.35rem;
		min-width: 0;
	}

	.output-cell {
		justify-content: flex-end;
	}

	/* ---- Port labels ---- */
	.port-name {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		line-height: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.port-name.missing {
		color: #fca5a5 !important;
	}

	/* ---- Handles (inline within port rows) ---- */
	/*
	 * SvelteFlow positions handles absolute relative to the node by default.
	 * By making port-row `position: relative` and overriding handle styles here,
	 * handles become absolute within their port row. SvelteFlow reads the actual
	 * DOM position via getBoundingClientRect(), so connection lines draw correctly.
	 */
	:global(.port-handle) {
		position: absolute !important;
		top: 50% !important;
		transform: translateY(-50%) !important;
		width: 0.7rem !important;
		height: 0.7rem !important;
		border: 2px solid rgba(232, 241, 250, 0.65) !important;
		border-radius: 50% !important;
		cursor: crosshair;
	}

	:global(.port-handle.missing) {
		border-color: rgba(252, 165, 165, 0.95) !important;
		box-shadow: 0 0 0 0.18rem rgba(220, 80, 80, 0.2);
	}

	/* Input handle sits at the node's left border */
	:global(.input-cell .port-handle) {
		left: -0.35rem !important;
	}

	/* Output handle sits at the node's right border */
	:global(.output-cell .port-handle) {
		right: -0.35rem !important;
		left: auto !important;
	}
</style>
