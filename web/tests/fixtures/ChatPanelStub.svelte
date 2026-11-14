<!--
	Stands in for the conversation in tests of whatever hosts it: inert, but it
	takes the keyboard when asked to, and can ask its host for an interface action
	the way a reply from the assistant would.
-->
<script lang="ts">
	import type { ActionSource, InterfaceAction } from '../../src/lib/interface-actions/actions';

	let {
		onInterfaceAction
	}: { onInterfaceAction?: (action: InterfaceAction, source: ActionSource) => void } = $props();
	let composer = $state<HTMLTextAreaElement | null>(null);

	const blendSetup: InterfaceAction = {
		kind: 'open_panel',
		panel: 'blend_setup',
		draft: { name: 'Kiremt blend', model_ids: [] }
	};

	export function focusComposer() {
		composer?.focus();
	}
</script>

<div data-testid="stub-component">
	<textarea bind:this={composer} aria-label="Ask the assistant"></textarea>
	<button type="button" onclick={() => onInterfaceAction?.(blendSetup, 'assistant')}>
		Assistant offers a blend setup
	</button>
</div>
