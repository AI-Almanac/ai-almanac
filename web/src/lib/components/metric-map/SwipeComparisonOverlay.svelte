<script lang="ts">
	import { modelRunLabel } from './mapUi';
	import type { RunDef } from './types';

	type Props = {
		left: RunDef | null;
		right: RunDef | null;
		selectedWindow: string;
		selectedReferenceWindow: string;
		swipePosition: number;
		draggingSwipe: boolean;
		windowLabelFor: (windowValue: string) => string;
		onStartDrag: (event: PointerEvent) => void;
		onKeyboardMove: (event: KeyboardEvent) => void;
	};

	let {
		left,
		right,
		selectedWindow,
		selectedReferenceWindow,
		swipePosition,
		draggingSwipe,
		windowLabelFor,
		onStartDrag,
		onKeyboardMove
	}: Props = $props();
</script>

{#if left && right}
	<div class="swipe-label swipe-label-left">
		<span>Left</span>
		<strong>{modelRunLabel(left)} · {windowLabelFor(selectedWindow)}</strong>
	</div>
	<div class="swipe-label swipe-label-right">
		<span>Right</span>
		<strong>{modelRunLabel(right)} · {windowLabelFor(selectedReferenceWindow)}</strong>
	</div>
{/if}

<div class="swipe-split" style="left: {swipePosition}%">
	<div class="swipe-line"></div>
	<div
		class="swipe-handle"
		class:dragging={draggingSwipe}
		onpointerdown={onStartDrag}
		onkeydown={onKeyboardMove}
		role="slider"
		tabindex="0"
		aria-label="Swipe comparison position"
		aria-valuemin="5"
		aria-valuemax="95"
		aria-valuenow={Math.round(swipePosition)}
	>
		<span>‹</span>
		<span>›</span>
	</div>
</div>

<style>
	.swipe-split {
		position: absolute;
		top: 0;
		bottom: 0;
		z-index: 24;
		pointer-events: none;
	}

	.swipe-line {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 2px;
		background: rgba(255, 255, 255, 0.92);
		box-shadow: 0 0 0 1px rgba(30, 37, 44, 0.35);
		transform: translateX(-1px);
	}

	.swipe-handle {
		position: absolute;
		top: 50%;
		left: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.1rem;
		width: 2.35rem;
		height: 2.35rem;
		border-radius: 999px;
		border: 1px solid rgba(35, 41, 46, 0.28);
		background: rgba(255, 255, 255, 0.94);
		color: #2f4f47;
		font-size: 1rem;
		font-weight: 800;
		box-shadow: 0 0.35rem 1.2rem rgba(0, 0, 0, 0.22);
		transform: translate(-50%, -50%);
		cursor: ew-resize;
		pointer-events: auto;
		user-select: none;
	}

	.swipe-handle.dragging,
	.swipe-handle:focus-visible {
		outline: none;
		border-color: var(--color-accent);
		box-shadow:
			0 0 0 3px rgba(67, 122, 111, 0.25),
			0 0.35rem 1.2rem rgba(0, 0, 0, 0.22);
	}

	.swipe-label {
		position: absolute;
		top: 1rem;
		z-index: 22;
		display: flex;
		align-items: baseline;
		gap: 0.35rem;
		max-width: min(12rem, 30%);
		padding: 0.32rem 0.5rem;
		border: 1px solid rgba(213, 207, 194, 0.86);
		border-radius: 0.4rem;
		background: rgba(255, 255, 255, 0.9);
		color: #2d2a25;
		box-shadow: 0 0.2rem 0.75rem rgba(0, 0, 0, 0.14);
		pointer-events: none;
	}

	.swipe-label-left {
		left: 1rem;
	}

	.swipe-label-right {
		right: 1rem;
	}

	.swipe-label span {
		font-size: 0.48rem;
		font-weight: 800;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: #7a756b;
	}

	.swipe-label strong {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.68rem;
	}
</style>
