<!--
	Stands in for a run's view and remembers which run it was created for, so a test
	can tell a view that was started afresh from one that was handed another run.
-->
<script lang="ts">
	import { untrack } from 'svelte';

	let { run }: { run: { key: string } } = $props();
	const createdFor = untrack(() => run.key);
</script>

<p data-testid="run-probe">showing {run.key}, created for {createdFor}</p>
