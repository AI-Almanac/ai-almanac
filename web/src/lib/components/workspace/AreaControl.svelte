<script lang="ts">
	import { failureMessage } from '$lib/api';
	import type { FocusAreaValue } from '$lib/api/jobs';
	import FocusAreaMap from '$lib/components/FocusAreaMap.svelte';
	import { studyAreaWords } from '$lib/workspace/study-area';
	import SettingPopover from './SettingPopover.svelte';

	interface Props {
		/** Where runs in this workspace are scored unless one names its own area. */
		area: FocusAreaValue | null;
		save: (area: FocusAreaValue | null) => Promise<void>;
	}

	let { area, save }: Props = $props();

	// The map edits its own copy; the workspace's area only seeds it.
	let draft = $state<FocusAreaValue | null>(null);
	let saving = $state(false);
	let problem = $state<string | null>(null);

	function startEditing() {
		draft = area;
		problem = null;
	}

	async function keep(close: () => void) {
		saving = true;
		problem = null;
		try {
			await save(draft);
			close();
		} catch (e) {
			problem = failureMessage(e);
		} finally {
			saving = false;
		}
	}
</script>

<SettingPopover label="Area" title="Change where runs here are scored" wide onOpen={startEditing}>
	{#snippet value()}{studyAreaWords(area)}{/snippet}
	{#snippet children(close)}
		<FocusAreaMap value={draft} onchange={(next) => (draft = next)} />
		{#if problem}<p class="problem" role="alert">{problem}</p>{/if}
		<div class="actions">
			<button type="button" class="save" disabled={saving} onclick={() => keep(close)}>
				{saving ? 'Saving…' : 'Save'}
			</button>
			<button type="button" class="quiet" onclick={close}>Cancel</button>
		</div>
		<p class="note">
			New benchmarks and blends here start in this area. Runs already made keep theirs.
		</p>
	{/snippet}
</SettingPopover>

<style>
	.actions {
		display: flex;
		gap: var(--space-2);
	}
	.save {
		border: 0;
		border-radius: 0.4rem;
		padding: 0.4rem 0.9rem;
		background: var(--color-accent);
		color: var(--color-on-accent, #fff);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	.quiet {
		border: 0;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		cursor: pointer;
	}
	.note,
	.problem {
		margin: 0;
		font-size: var(--text-size-label);
	}
	.note {
		color: var(--color-text-muted);
	}
	.problem {
		color: var(--color-danger, #b42318);
	}
</style>
