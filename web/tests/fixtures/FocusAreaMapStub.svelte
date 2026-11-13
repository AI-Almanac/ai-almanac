<!-- Stands in for the area map: shows the area it was given, and picks one when asked. -->
<script lang="ts">
	import type { FocusAreaValue } from '../../src/lib/api/jobs';

	interface Props {
		value: FocusAreaValue | null;
		onchange: (next: FocusAreaValue | null) => void;
	}

	let { value, onchange }: Props = $props();
</script>

<p data-testid="map-area">{JSON.stringify(value)}</p>
<button type="button" onclick={() => onchange({ level: 'adm1', units: ['Tigray'] })}>
	Pick Tigray
</button>
