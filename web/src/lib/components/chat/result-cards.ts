import type { Component } from 'svelte';
import { resultKind, type ResultKind } from '$lib/chat/tools';
import InterfaceActionCard from './InterfaceActionCard.svelte';
import ResultTableCard from './ResultTableCard.svelte';
import SubmittedJobsCard from './SubmittedJobsCard.svelte';
import type { ResultCardProps } from './step-context';

const RESULT_CARDS: Record<ResultKind, Component<ResultCardProps>> = {
	'interface-action': InterfaceActionCard,
	'submitted-jobs': SubmittedJobsCard,
	table: ResultTableCard
};

export function resultCardFor(toolName: string): Component<ResultCardProps> | null {
	const kind = resultKind(toolName);
	return kind ? RESULT_CARDS[kind] : null;
}
