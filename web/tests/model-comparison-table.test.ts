import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { Job } from '../src/lib/api';
import ModelComparisonTable from '../src/lib/components/ModelComparisonTable.svelte';
import { metricsFor } from './fixtures/workspace';

describe('the models side by side', () => {
	it('lists two runs of the same model as two rows', () => {
		const jobs = [
			{ id: 'j-1', model_name: 'aifs', model_display_name: 'AIFS' },
			{ id: 'j-2', model_name: 'aifs', model_display_name: 'AIFS' }
		] as Job[];
		const metrics = [
			metricsFor('j-1', 'aifs', { '1-15': 6.4 }, { '1-15': 11.9 }),
			metricsFor('j-2', 'aifs', { '1-15': 7.1 }, { '1-15': 11.9 })
		];

		render(ModelComparisonTable, { jobs, metrics, definitions: [] });

		expect(screen.getAllByRole('rowheader', { name: 'AIFS' })).toHaveLength(2);
	});
});
