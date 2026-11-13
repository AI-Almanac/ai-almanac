<script lang="ts">
	import { exportMapImage, type CapturableMap, type MapImageText } from './exportImage';

	interface Props {
		/** The map to picture, or null before it exists. */
		map: () => CapturableMap | null;
		/** What the picture says, read when the export starts. */
		text: () => MapImageText;
		/** Nothing worth picturing is drawn yet. */
		disabled?: boolean;
	}

	const { map, text, disabled = false }: Props = $props();

	let exporting = $state(false);
	let failed = $state(false);

	async function exportMap() {
		const shown = map();
		if (!shown || exporting) return;
		exporting = true;
		failed = false;
		try {
			await exportMapImage(shown, text());
		} catch {
			failed = true;
		} finally {
			exporting = false;
		}
	}
</script>

<div class="map-export">
	<button
		type="button"
		disabled={disabled || exporting}
		title="Download this map as a picture with its title and colour scale"
		onclick={exportMap}
	>
		{exporting ? 'Exporting…' : 'Export map'}
	</button>
	{#if failed}
		<p role="alert">The map could not be exported. Try again once it has finished drawing.</p>
	{/if}
</div>

<style>
	.map-export {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 0.3rem;
	}

	button {
		padding: 0.25rem 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
	}

	button:disabled {
		cursor: default;
		opacity: 0.6;
	}

	p {
		margin: 0;
		color: var(--color-danger);
		font-size: var(--text-size-caption);
	}
</style>
