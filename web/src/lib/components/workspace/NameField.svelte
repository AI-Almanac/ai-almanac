<script lang="ts">
	import { untrack } from 'svelte';
	import { failureMessage } from '$lib/api';
	import { nameEdit } from '$lib/workspace/rename';

	interface Props {
		/** The name shown now, which editing starts from. */
		value: string;
		/** Saves the new name; `null` asks for the default name back. */
		onSave: (title: string | null) => Promise<void>;
		onClose: () => void;
	}

	let { value, onSave, onClose }: Props = $props();

	let draft = $state(untrack(() => value));
	let error = $state<string | null>(null);
	let saving = $state(false);
	// Closing removes the field, which can fire a late blur; that blur must not save.
	let closed = false;

	function close() {
		closed = true;
		onClose();
	}

	async function save() {
		if (closed || saving) return;
		const edit = nameEdit(draft, value);
		if (edit.kind === 'unchanged') return close();
		saving = true;
		try {
			await onSave(edit.title);
			close();
		} catch (e) {
			error = failureMessage(e);
		} finally {
			saving = false;
		}
	}

	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			void save();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			close();
		}
	}

	function focusAndSelect(input: HTMLInputElement) {
		input.focus();
		input.select();
	}
</script>

<span class="name-field">
	<input
		type="text"
		aria-label="New name (leave empty for the original name)"
		aria-invalid={error ? 'true' : undefined}
		readonly={saving}
		bind:value={draft}
		{onkeydown}
		onblur={() => void save()}
		use:focusAndSelect
	/>
	{#if error}<span class="error" role="alert">{error}</span>{/if}
</span>

<style>
	.name-field {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: 0.15rem;
		min-width: 0;
	}
	input {
		min-width: 0;
		padding: 0.1rem 0.3rem;
		border: 1px solid var(--color-accent-border);
		border-radius: 0.3rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	input:focus {
		outline: none;
		border-color: var(--color-accent);
	}
	input[aria-invalid='true'] {
		border-color: var(--color-danger);
	}
	.error {
		color: var(--color-danger);
		font-size: var(--text-size-caption);
		font-weight: 400;
	}
</style>
