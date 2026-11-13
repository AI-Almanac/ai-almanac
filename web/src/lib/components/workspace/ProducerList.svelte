<script lang="ts">
	import type { ArtifactProvenance } from '$lib/api';
	import { producerSteps, stepAttribution } from '$lib/workspace/lineage';

	interface Props {
		provenance: ArtifactProvenance;
		onOpenTurn?: (turnId: string) => void;
	}

	let { provenance, onOpenTurn }: Props = $props();

	const producers = $derived(producerSteps(provenance));
</script>

{#if producers.length > 1}
	<section class="producers">
		<h4>
			This exact file was made {producers.length} times
		</h4>
		<ul>
			{#each producers as step (step.id)}
				<li>
					<span>{stepAttribution(step)} · {new Date(step.created_at).toLocaleString()}</span>
					{#if step.turn_id && onOpenTurn}
						<button type="button" class="link" onclick={() => onOpenTurn(step.turn_id!)}>
							Show in chat
						</button>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.producers {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	h4 {
		margin: 0;
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--color-text-muted);
	}
	ul {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		margin: 0;
		padding-left: 1.1rem;
		font-size: 0.76rem;
		color: var(--color-text-muted);
	}
	li {
		overflow-wrap: anywhere;
	}
	.link {
		margin-left: 0.5rem;
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		cursor: pointer;
	}
</style>
