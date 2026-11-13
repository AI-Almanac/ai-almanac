<script lang="ts">
	import { onMount } from 'svelte';

	import { clampPane, type PaneLimits } from '$lib/components/split-width';

	interface Props {
		/** The chosen width in pixels, or null for the layout's default. */
		size?: number | null;
		/** The space the pane shares, measured when needed. */
		available: () => number;
		limits: PaneLimits;
		label: string;
		/** Distinguishes the saved size per pane. */
		storageKey: string;
		/** Which neighbour the handle sizes: the pane before it or the pane after it. */
		pane?: 'before' | 'after';
	}

	let {
		size = $bindable(null),
		available,
		limits,
		label,
		storageKey,
		pane = 'after'
	}: Props = $props();

	// A pane after the handle grows as the handle moves left; one before it, as it moves right.
	const growth = $derived(pane === 'after' ? -1 : 1);

	const key = $derived(`almanac.pane.${storageKey}`);
	let dragging = $state(false);
	let handle = $state<HTMLElement | null>(null);
	// What assistive technology is told: measured, since the default width is the layout's.
	let reported = $state<{ now: number; max: number } | null>(null);

	function report() {
		if (!handle) return;
		reported = {
			now: Math.round(size ?? currentSize(handle)),
			max: clampPane(Infinity, available(), limits)
		};
	}

	onMount(() => {
		const saved = Number(localStorage.getItem(key));
		if (Number.isFinite(saved) && saved > 0) size = saved;
		report();
	});

	function commit(next: number) {
		size = clampPane(next, available(), limits);
		localStorage.setItem(key, String(size));
		report();
	}

	function reset() {
		size = null;
		localStorage.removeItem(key);
		// The layout's default width only shows once the pane has been laid out again.
		requestAnimationFrame(report);
	}

	/** The pane's size before any choice, read from the element it sits against. */
	function currentSize(from: HTMLElement): number {
		const sized = (
			pane === 'after' ? from.nextElementSibling : from.previousElementSibling
		) as HTMLElement | null;
		return sized ? sized.clientWidth : limits.min;
	}

	function grab(event: PointerEvent) {
		event.preventDefault();
		const target = event.currentTarget as HTMLElement;
		const start = { x: event.clientX, size: size ?? currentSize(target) };
		dragging = true;
		target.setPointerCapture(event.pointerId);

		const move = (e: PointerEvent) => commit(start.size + growth * (e.clientX - start.x));
		const release = () => {
			dragging = false;
			target.removeEventListener('pointermove', move);
			target.removeEventListener('pointerup', release);
			target.removeEventListener('pointercancel', release);
		};
		target.addEventListener('pointermove', move);
		target.addEventListener('pointerup', release);
		target.addEventListener('pointercancel', release);
	}

	// A pointer-only splitter is unreachable by keyboard, and the panes have no
	// other size control.
	function nudge(event: KeyboardEvent) {
		const step = event.shiftKey ? 64 : 16;
		const current = size ?? currentSize(event.currentTarget as HTMLElement);
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			commit(current - growth * step);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			commit(current + growth * step);
		} else if (event.key === 'Home') {
			event.preventDefault();
			reset();
		}
	}
</script>

<!-- The W3C window-splitter pattern: a focusable separator driven by arrow keys.
     svelte-check reads "separator" as decorative and flags the handlers, so the
     rules are silenced rather than the keyboard support dropped. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	bind:this={handle}
	class="resizer"
	class:dragging
	role="separator"
	aria-orientation="vertical"
	aria-label="{label} (arrow keys, Home to reset)"
	aria-valuenow={reported?.now}
	aria-valuemin={limits.min}
	aria-valuemax={reported?.max}
	tabindex="0"
	onpointerdown={grab}
	onkeydown={nudge}
	onfocus={report}
	ondblclick={reset}
></div>

<style>
	/* Sits in the gap between panes: a thin line with a comfortable hit area. */
	.resizer {
		position: relative;
		z-index: 1;
		flex: none;
		align-self: stretch;
		width: 0.75rem;
		margin: 0 -0.375rem;
		border-radius: 999px;
		background: transparent;
		cursor: col-resize;
		touch-action: none;
	}
	.resizer::after {
		content: '';
		position: absolute;
		inset: 0 calc(50% - 1px);
		border-radius: 999px;
		background: var(--color-border);
		transition:
			background-color 0.12s,
			inset 0.12s;
	}
	.resizer:hover::after,
	.resizer:focus-visible::after,
	.resizer.dragging::after {
		inset: 0 calc(50% - 2px);
		background: var(--color-accent);
	}
	.resizer:focus-visible {
		outline: 0.125rem solid var(--color-accent);
		outline-offset: -0.125rem;
	}
	@media (max-width: 1050px) {
		/* The zones stack at this width, so there is no edge to drag. */
		.resizer {
			display: none;
		}
	}
</style>
