<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		getCapabilities,
		listAddableJobs,
		type ChatMessage,
		type RunOrigin,
		type WorkspaceArtifact
	} from '$lib/api';
	import { account } from '$lib/account.svelte';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import AnalysisFiles from '$lib/components/workspace/AnalysisFiles.svelte';
	import AssistantStrip from '$lib/components/workspace/AssistantStrip.svelte';
	import NewRunMenu from '$lib/components/workspace/NewRunMenu.svelte';
	import PaneResizer from '$lib/components/workspace/PaneResizer.svelte';
	import ProductsList from '$lib/components/workspace/ProductsList.svelte';
	import ProductsStrip from '$lib/components/workspace/ProductsStrip.svelte';
	import WorkspacePanel from '$lib/components/workspace/WorkspacePanel.svelte';
	import WorkspaceCanvas from '$lib/components/workspace/WorkspaceCanvas.svelte';
	import WorkspaceStart from '$lib/components/workspace/WorkspaceStart.svelte';
	import WorkspaceSummaryStrip from '$lib/components/workspace/WorkspaceSummaryStrip.svelte';
	import ZoneToggle from '$lib/components/workspace/ZoneToggle.svelte';
	import {
		dispatchAction,
		type ActionHandlers,
		type ActionSource,
		type InterfaceAction,
		type WorkspaceTarget
	} from '$lib/interface-actions/actions';
	import { analysisHeading } from '$lib/workspace/analyses';
	import { canvasQuery, parseCanvasView, type CanvasView } from '$lib/workspace/canvas-view';
	import { newRunEntries } from '$lib/workspace/new-run-menu';
	import { opened, type OpenPanel, type PanelRequest } from '$lib/workspace/panels';
	import {
		allProducts,
		analysisOnStage,
		productGroups,
		productView,
		shownProductKey,
		stageView,
		type Product,
		type ProductKind
	} from '$lib/workspace/products';
	import { jobStatusMap, type Run } from '$lib/workspace/runs';
	import { seasonLabel } from '$lib/workspace/season-form';
	import { checkArrivals, fileView, jobsView, targetView } from '$lib/workspace/stage-follow';
	import { starterQuestions } from '$lib/workspace/starter-prompts';
	import { updateForecastPrompt } from '$lib/workspace/summary';
	import { WorkspaceLayout } from '$lib/workspace/workspace-layout.svelte';
	import { WorkspaceSession } from '$lib/workspace/workspace-session.svelte';
	import { assistantLimits, PRODUCTS_LIMITS } from '$lib/workspace/zones';

	const id = $derived($page.params.id ?? '');
	const session = $derived(new WorkspaceSession(id));
	const workspace = $derived(session.workspace);
	const summary = $derived(session.summary);
	const layout = new WorkspaceLayout();

	// A request the page puts to the assistant on the user's behalf; the nonce sends it once.
	let prompt = $state<string | null>(null);
	let promptNonce = $state(0);
	let transcript = $state<ChatMessage[]>([]);
	let chatAvailable = $state(false);
	let assistantWorking = $state(false);
	let shellEl = $state<HTMLElement | null>(null);
	let pageEl = $state<HTMLElement | null>(null);
	// What was opened by hand, mirrored in the URL; the overview means nothing was.
	let view = $state<CanvasView>(untrack(() => parseCanvasView($page.url.searchParams)));
	// The newest product to arrive while nothing was opened by hand.
	let followed = $state<CanvasView | null>(null);
	let focusTurnId = $state<string | null>(null);
	let conversation = $state<{ focusComposer: () => void } | null>(null);
	let assistantOpener = $state<{ focus: () => void } | null>(null);
	let productsList = $state<{ focus: () => void } | null>(null);
	let productsStrip = $state<{ focus: () => void } | null>(null);
	// The setup form or run picker open over the stage; the stage stays as it was beneath.
	let stagePanel = $state<OpenPanel | null>(null);

	const newEntries = $derived(newRunEntries(account.canUseForecasting));
	const results = $derived(session.results);
	const groups = $derived(
		productGroups(results, (analysis) =>
			analysisHeading(analysis, transcript, workspace?.analyses ?? [])
		)
	);
	const products = $derived(allProducts(groups));
	const starterPrompts = $derived(
		starterQuestions({
			hasProducts: products.length > 0,
			seasonName: summary && !summary.season.is_default ? summary.season.name : null
		})
	);
	const shown = $derived(stageView(view, followed, results, products));
	const shownKey = $derived(shownProductKey(shown, results));
	const stageAnalysis = $derived(analysisOnStage(shown, results));
	const jobStatuses = $derived(jobStatusMap(workspace?.jobs ?? []));
	const fileTitles = $derived(
		Object.fromEntries((workspace?.artifacts ?? []).map((file) => [file.id, file.title]))
	);

	$effect(() => {
		if (account.loaded && !account.canUseWorkspaces) goto('/');
	});

	$effect(() => {
		if (pageEl) return layout.track(pageEl);
	});

	$effect(() => {
		const current = session;
		const updates = new AbortController();
		untrack(() => void current.open(updates.signal));
		return () => updates.abort();
	});

	onMount(() => {
		layout.restore(localStorage, window.innerWidth);
		getCapabilities()
			.then((caps) => (chatAvailable = caps.chat))
			.catch(() => (chatAvailable = false));
	});

	// The stage follows new work until something is opened by hand.
	let knownProducts: Set<string> | null = null;
	$effect(() => {
		if (!workspace) return;
		const check = checkArrivals(knownProducts, products);
		knownProducts = check.known;
		if (check.arrived && untrack(() => view.kind === 'overview')) followed = check.arrived;
	});

	function openPanel(request: PanelRequest) {
		stagePanel = opened(request, stagePanel);
	}

	/** Opened or collapsed by hand: the keyboard lands on the control that undoes it. */
	async function toggleProducts(open: boolean, at: ProductKind | null = null) {
		layout.setProductsOpen(open, at);
		await tick();
		(open ? productsList : productsStrip)?.focus();
	}

	/** Opened or closed by hand: typing starts in the composer, and closing returns to what opened it. */
	async function toggleAssistant(open: boolean) {
		layout.setAssistantOpen(open);
		await tick();
		if (open) conversation?.focusComposer();
		else assistantOpener?.focus();
	}

	function transcriptChanged(messages: ChatMessage[]) {
		layout.transcriptGrew(transcript.length, messages.length);
		transcript = messages;
	}

	// Clear first so clicking the same turn again still scrolls to it.
	async function focusTurn(turnId: string) {
		layout.setAssistantOpen(true);
		focusTurnId = null;
		await tick();
		focusTurnId = turnId;
	}

	function ask(question: string) {
		prompt = question;
		promptNonce += 1;
		layout.setAssistantOpen(true);
	}

	function updateForecast() {
		if (summary?.latest_forecast) ask(updateForecastPrompt(summary.latest_forecast));
	}

	/** Opening something by hand: it is recorded in the URL and the stage stops following. */
	function navigate(next: CanvasView) {
		stagePanel = null;
		view = next;
		followed = null;
		goto(canvasQuery(next, $page.url.searchParams), {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}

	function openProduct(product: Product) {
		const next = productView(product);
		if (next) navigate(next);
	}

	function showJob(jobId: string) {
		const next = jobsView(session.results.runs, [jobId]);
		if (next) navigate(next);
	}

	/** Put runs into the workspace and show the first of them on the stage. */
	async function attachRun(jobIds: string[], origin: RunOrigin) {
		await session.attach(jobIds, origin);
		stagePanel = null;
		if (jobIds.length > 0) showJob(jobIds[0]);
	}

	const addExistingRun = (run: Run) =>
		attachRun(
			run.jobs.map((job) => job.id),
			'added'
		);

	/** The chat may mention something made moments ago, so a miss earns one reload. */
	async function openItem(target: WorkspaceTarget) {
		const found = workspace ? targetView(target, results.runs, workspace.artifacts) : null;
		if (found) return navigate(found);
		const reloaded = await session.refresh();
		const retried = reloaded ? targetView(target, session.results.runs, reloaded.artifacts) : null;
		if (retried) navigate(retried);
	}

	// What this page does when the assistant, or a button in the conversation, asks.
	const actionHandlers: ActionHandlers = {
		open_panel: (action) => openPanel(action),
		show: (action) => void openItem(action.target)
	};

	/** A form the user has open is theirs: the assistant's action waits as its button in the conversation. */
	function takeAction(action: InterfaceAction, source: ActionSource) {
		if (source === 'assistant' && stagePanel) return;
		dispatchAction(action, actionHandlers);
	}
</script>

{#snippet newRun(compact: boolean)}
	<NewRunMenu entries={newEntries} onChoose={(panel) => openPanel({ panel })} {compact} />
{/snippet}

<div
	class="workspace-page workspace-dense"
	class:under-assistant-bar={chatAvailable}
	bind:this={pageEl}
	style:--page-top="{layout.pageTop}px"
>
	<div class="workspace-main">
		{#if session.error && !workspace}
			<p class="error">{session.error}</p>
		{:else if !workspace}
			<p class="muted">Loading…</p>
		{:else}
			<header class="ws-header">
				<div class="ws-title">
					<a class="muted" href="/workspaces">← Workspaces</a>
					<h1>{workspace.name}</h1>
					{#if workspace.settings.season && !summary}
						<span class="muted ws-season">{seasonLabel(workspace.settings.season)}</span>
					{/if}
					{#if workspace.description}
						<p class="muted ws-description" title={workspace.description}>
							{workspace.description}
						</p>
					{/if}
				</div>
				{#if summary}
					<WorkspaceSummaryStrip
						{summary}
						canAct={chatAvailable}
						onUpdateForecast={updateForecast}
						onOpenJob={showJob}
						seasonEditor={{
							load: () => session.seasonInForce(),
							save: (season) => session.saveSettings({ season })
						}}
						areaEditor={{
							area: workspace.settings.study_area ?? null,
							save: (study_area) => session.saveSettings({ study_area })
						}}
					/>
				{/if}
			</header>

			<!-- Products, the stage, then the assistant. Together they fill the screen
			     below the header and each scrolls inside itself. -->
			<div
				class="workspace-shell"
				bind:this={shellEl}
				style:--products-width={layout.widths.products}
				style:--assistant-width={layout.widths.assistant}
			>
				{#if products.length > 0}
					<aside class="zone products-zone" class:collapsed={!layout.zones.productsOpen}>
						{#if layout.zones.productsOpen}
							<ProductsList
								bind:this={productsList}
								{groups}
								{shownKey}
								openAt={layout.productsOpenAt}
								onOpen={openProduct}
								onCollapse={() => toggleProducts(false)}
								onOpenTurn={chatAvailable ? focusTurn : undefined}
								onRenameAnalysis={(turnId, title) => session.renameAnalysis(turnId, title)}
								onRefresh={() => {
									session.refresh();
									session.refreshSummary();
								}}
								{newRun}
							/>
						{:else}
							<ProductsStrip
								bind:this={productsStrip}
								{groups}
								onExpand={(kind) => toggleProducts(true, kind)}
								{newRun}
							/>
						{/if}
					</aside>
					{#if layout.zones.productsOpen}
						<PaneResizer
							bind:size={layout.productsWidth}
							pane="before"
							available={() => shellEl?.clientWidth ?? 0}
							limits={PRODUCTS_LIMITS}
							label="Resize the products list"
							storageKey="workspace.productsWidth"
						/>
					{/if}
				{/if}

				<section class="zone stage">
					<!-- Out of reach of the keyboard while a panel is open over it. -->
					<div class="stage-content" inert={stagePanel !== null}>
						{#if products.length === 0 && shown.kind === 'overview'}
							<WorkspaceStart
								entries={newEntries}
								onNew={(panel) => openPanel({ panel })}
								assistantAvailable={chatAvailable}
								onOpenAssistant={() => toggleAssistant(true)}
							/>
						{:else}
							{#if shown.kind === 'file' && stageAnalysis && stageAnalysis.files.length > 1}
								<AnalysisFiles
									analysis={stageAnalysis}
									shownId={shown.artifactId}
									onOpen={(file: WorkspaceArtifact) => navigate(fileView(file))}
								/>
							{/if}
							<WorkspaceCanvas
								{workspace}
								{results}
								view={shown}
								onNavigate={navigate}
								onOpenTurn={chatAvailable ? focusTurn : undefined}
								onRenameFile={(file, title) => session.renameFile(file, title)}
							/>
						{/if}
					</div>
					{#if stagePanel}
						<WorkspacePanel
							open={stagePanel}
							jobs={workspace.jobs}
							area={workspace.settings.study_area ?? null}
							onStarted={(jobIds) => attachRun(jobIds, 'started')}
							loadAddableJobs={() => listAddableJobs(id)}
							onAddExisting={addExistingRun}
							onClose={() => (stagePanel = null)}
						/>
					{/if}
				</section>

				{#if chatAvailable}
					{#if layout.assistantOpen && layout.form === 'column'}
						<PaneResizer
							bind:size={layout.assistantWidth}
							available={() => shellEl?.clientWidth ?? 0}
							limits={assistantLimits(layout.comparing)}
							label="Resize the assistant"
							storageKey="workspace.sideWidth"
						/>
					{/if}
					<!-- A column beside the stage; on a narrow screen, a bar along the bottom
					     that opens into a sheet. -->
					<aside
						class="zone assistant-zone"
						class:collapsed={!layout.assistantOpen}
						aria-label="Assistant"
					>
						{#if !layout.assistantOpen}
							<AssistantStrip
								bind:this={assistantOpener}
								working={assistantWorking}
								unread={layout.unreadReply}
								onExpand={() => toggleAssistant(true)}
							/>
						{/if}
						<!-- Kept mounted while collapsed so a running turn, the scroll position
						     and a draft all survive. -->
						<div class="assistant-body" hidden={!layout.assistantOpen}>
							<header class="assistant-head">
								<h2>Assistant</h2>
								<ZoneToggle
									label="Collapse the assistant"
									toward={layout.form === 'sheet' ? 'down' : 'right'}
									onclick={() => toggleAssistant(false)}
								/>
							</header>
							<ChatPanel
								bind:this={conversation}
								jobs={[]}
								scopeKind="workspace"
								scopeKey={id}
								title={workspace.name}
								emptyMessage="Ask about what is here, run a benchmark or blend, or plot a metric. Everything produced shows up under Products with a record of how it was made."
								placeholder="Ask the assistant… (Enter to send, Shift+Enter for newline)"
								suggestions={starterPrompts}
								{jobStatuses}
								followUps={session.followUps}
								externalPrompt={prompt}
								externalPromptNonce={promptNonce}
								onCancelFollowUp={(followUpId) => session.cancelFollowUp(followUpId)}
								{focusTurnId}
								onOpenItem={openItem}
								onInterfaceAction={takeAction}
								{fileTitles}
								onComparingChange={(value) => (layout.comparing = value)}
								onTranscriptChange={transcriptChanged}
								onWorkingChange={(working) => (assistantWorking = working)}
							/>
						</div>
					</aside>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	/* The page is exactly the screen below the navigation, so nothing but the
	   zones scrolls and the layout never slides under the navigation. */
	.workspace-page {
		align-items: stretch;
		height: calc(100dvh - var(--page-top));
		min-height: 26rem;
		padding: var(--space-3) 0 var(--space-4);
	}
	.workspace-main {
		display: flex;
		flex-direction: column;
		min-height: 0;
	}

	/* The title and the workspace's headline facts share one row, wrapping only
	   when the window is too narrow for both. */
	.ws-header {
		display: flex;
		flex: none;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1) var(--space-4);
		margin-bottom: var(--space-3);
		min-width: 0;
	}
	.ws-title {
		display: flex;
		flex: 0 1 auto;
		align-items: baseline;
		gap: var(--space-3);
		min-width: 0;
	}
	.ws-header > :global(.summary) {
		flex: 1 1 24rem;
		min-width: 0;
	}
	.ws-title h1 {
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-title);
		white-space: nowrap;
	}
	.ws-title a,
	.ws-season {
		flex-shrink: 0;
		white-space: nowrap;
	}
	.ws-description {
		flex: 0 1 18rem;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.workspace-shell {
		display: flex;
		flex: 1;
		gap: var(--space-4);
		min-height: 0;
	}
	.zone {
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
		border: 1px solid var(--color-border);
		border-radius: 0.6rem;
		background: var(--color-surface);
	}
	.products-zone {
		flex: none;
		width: var(--products-width);
		max-width: 35%;
	}
	.stage {
		position: relative;
		flex: 1 1 0;
		gap: var(--space-3);
		padding: var(--space-3) var(--space-4);
	}
	.stage-content {
		/* What is inside stacks among itself, so nothing of it shows through a panel opened over it. */
		isolation: isolate;
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: var(--space-3);
		min-width: 0;
		min-height: 0;
	}
	.assistant-zone {
		flex: none;
		width: var(--assistant-width);
		max-width: 60%;
	}
	.assistant-body {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-height: 0;
	}
	.assistant-body[hidden] {
		display: none;
	}
	/* The same place and shape as the products list's heading across the stage. */
	.assistant-head {
		display: flex;
		flex: none;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-3) var(--space-2) var(--space-2) var(--space-4);
		border-bottom: 1px solid var(--color-border);
	}
	.assistant-head h2 {
		flex: 1;
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-lead);
	}
	.assistant-body > :global(.chat-panel) {
		flex: 1;
		min-height: 0;
		border: 0;
		border-radius: 0;
		box-shadow: none;
	}
	@media (max-width: 1050px) {
		/* Stacked and scrolling as a page: the stage first, then the products. */
		.workspace-page {
			height: auto;
			min-height: 0;
		}
		.workspace-page.under-assistant-bar {
			padding-bottom: calc(var(--assistant-bar-height) + var(--space-4));
		}
		.workspace-shell {
			flex: none;
			flex-direction: column;
		}
		.stage {
			order: -1;
			flex: none;
			height: calc(100dvh - var(--nav-height, 0rem) - var(--assistant-bar-height) - 2rem);
			min-height: 28rem;
		}
		.products-zone {
			width: auto;
			max-width: none;
		}
		.products-zone:not(.collapsed) {
			max-height: 60vh;
		}
		/* The assistant leaves the page: a bar along the bottom of the screen that
		   opens into a sheet over the lower part of it. */
		.assistant-zone {
			position: fixed;
			right: 0;
			bottom: 0;
			left: 0;
			z-index: 40;
			width: auto;
			max-width: none;
			border-width: 1px 0 0;
			border-radius: 0.9rem 0.9rem 0 0;
			box-shadow: 0 -0.5rem 2rem rgba(36, 33, 29, 0.14);
			padding-bottom: env(safe-area-inset-bottom, 0);
			overflow: hidden;
		}
		/* Dynamic viewport units, so an on-screen keyboard or a collapsing address
		   bar never pushes the composer off the screen; never over the navigation. */
		.assistant-zone:not(.collapsed) {
			height: min(70dvh, 100dvh - var(--nav-height, 0rem));
			overscroll-behavior: contain;
		}
	}
	.muted {
		margin: 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
	.error {
		color: var(--color-danger);
	}
</style>
