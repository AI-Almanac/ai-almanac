<script lang="ts">
	import { EVENT_TYPES } from '$lib/data/event-types';
	import RunSidebar, {
		type RunListItem,
		type RunSection,
		type RunStatus
	} from '$lib/components/RunSidebar.svelte';
	import type { BenchmarkStore } from '$lib/benchmarks.svelte';

	interface Props {
		store: BenchmarkStore;
		onNewBenchmark: () => void;
		onSelectGroup: (key: string) => void;
	}

	const { store, onNewBenchmark, onSelectGroup }: Props = $props();

	function eventLabel(eventType: string): string {
		return EVENT_TYPES.find((event) => event.id === eventType)?.name ?? eventType;
	}

	function formatRunDate(value: string): string {
		if (!value) return 'Unknown date';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat(undefined, {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		}).format(date);
	}

	function groupStatus(group: (typeof store.runGroups)[number]): RunStatus {
		if (
			group.jobs.some((job) => ['queued', 'starting', 'running', 'canceling'].includes(job.status))
		)
			return 'running';
		if (group.jobs.every((job) => job.status === 'complete')) return 'complete';
		if (group.jobs.every((job) => job.status === 'canceled')) return 'canceled';
		if (group.jobs.every((job) => job.status === 'failed')) return 'failed';
		return 'mixed';
	}

	function toItem(group: (typeof store.runGroups)[number]): RunListItem {
		return {
			id: group.key,
			title: group.region,
			meta: `${formatRunDate(group.mostRecentAt)} · ${eventLabel(group.eventType)}`,
			count: group.jobs.length,
			status: groupStatus(group),
			canDelete: group.isOwner
		};
	}

	const sections = $derived.by<RunSection[]>(() => {
		if (store.runGroups.length === 0) return [];
		const mine = store.runGroups.filter((g) => g.isOwner);
		const shared = store.runGroups.filter((g) => !g.isOwner);
		const result: RunSection[] = [
			{
				title: 'My Benchmarks',
				items: mine.map(toItem),
				open: true,
				emptyLabel: 'No benchmarks yet.'
			}
		];
		if (shared.length > 0) {
			result.push({ title: 'Shared With Me', items: shared.map(toItem), open: false });
		}
		return result;
	});

	const selectedId = $derived(store.showForm ? null : store.selectedGroupKey);
</script>

<RunSidebar
	newLabel="New benchmark"
	newActive={store.showForm}
	{selectedId}
	{sections}
	onNew={onNewBenchmark}
	onSelect={onSelectGroup}
	onDelete={(key) => store.deleteGroup(key)}
	deleteTitle="Delete run set"
/>
