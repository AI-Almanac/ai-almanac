<script lang="ts">
	import { onMount } from 'svelte';

	import { clampAside, effectiveAside, MIN_ASIDE_PX } from './split-width';

	interface Props {
		/** The grid whose second column this drags. Gets `--aside-width` set on it. */
		container: HTMLElement | null;
		/** An open comparison enforces its own minimum width. */
		comparing?: boolean;
		/** Distinguishes the saved width per layout (benchmark results, blend detail…). */
		storageKey: string;
	}

	const { container, comparing = false, storageKey }: Props = $props();

	const key = $derived(`almanac.splitWidth.${storageKey}`);
	let width = $state(0);
	let dragging = $state(false);

	onMount(() => {
		const saved = Number(localStorage.getItem(key));
		if (Number.isFinite(saved) && saved > 0) width = saved;
	});

	// Writing a custom property rather than a Svelte style keeps the grid
	// definition in layout.css, where the rest of the workspace layout lives.
	$effect(() => {
		if (!container) return;
		const available = container.clientWidth;
		if (!available) return;
		if (!width) {
			container.style.removeProperty('--aside-width');
			if (comparing)
				container.style.setProperty('--aside-width', `${effectiveAside(0, available, true)}px`);
			return;
		}
		container.style.setProperty(
			'--aside-width',
			`${effectiveAside(width, available, comparing)}px`
		);
	});

	function commit(next: number) {
		if (!container) return;
		width = clampAside(next, container.clientWidth);
		localStorage.setItem(key, String(width));
	}

	function grab(event: PointerEvent) {
		if (!container) return;
		event.preventDefault();
		dragging = true;
		const right = container.getBoundingClientRect().right;
		const target = event.currentTarget as HTMLElement;
		target.setPointerCapture(event.pointerId);

		const move = (e: PointerEvent) => commit(right - e.clientX);
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

	// Keyboard is not a nicety here: a pointer-only splitter is unreachable for
	// anyone navigating by keyboard, and the panes have no other size control.
	function nudge(event: KeyboardEvent) {
		const step = event.shiftKey ? 64 : 16;
		const current =
			width || container?.querySelector('.workspace-aside')?.clientWidth || MIN_ASIDE_PX;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			commit(current + step);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			commit(current - step);
		} else if (event.key === 'Home') {
			event.preventDefault();
			width = 0;
			localStorage.removeItem(key);
		}
	}
</script>

<!-- A focusable separator is the W3C window-splitter pattern: role="separator"
     with tabindex and arrow keys. svelte-check reads "separator" as decorative
     and flags the handlers, so the rules are silenced here rather than the
     keyboard support dropped. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	class="resizer"
	class:dragging
	role="separator"
	aria-orientation="vertical"
	aria-label="Resize the assistant panel (arrow keys, Home to reset)"
	tabindex="0"
	onpointerdown={grab}
	onkeydown={nudge}
	ondblclick={() => {
		width = 0;
		localStorage.removeItem(key);
	}}
></div>

<style>
	.resizer {
		/* Sits in the grid gap: a thin visible line with a comfortable hit area. */
		width: 0.75rem;
		margin: 0 -0.375rem;
		align-self: stretch;
		min-height: 6rem;
		cursor: col-resize;
		background: transparent;
		border: none;
		border-radius: 999px;
		position: relative;
		z-index: 1;
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
		background: var(--color-accent);
		inset: 0 calc(50% - 2px);
	}
	.resizer:focus-visible {
		outline: none;
	}

	@media (max-width: 1050px) {
		/* The split stacks at this width, so there is no border to drag. */
		.resizer {
			display: none;
		}
	}
</style>
