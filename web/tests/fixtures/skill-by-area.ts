import type { AreaSkillRow, SkillByArea } from '../../src/lib/api/skill-by-area';

export function skillRow(overrides: Partial<AreaSkillRow>): AreaSkillRow {
	return {
		area: 'West Gojjam',
		parent: 'Amhara',
		model: 'aifs',
		window: '1-15',
		cells: 12,
		onset_error_days: 6.2,
		false_alarm_rate: 0.3,
		miss_rate: 0.1,
		climatology: { onset_error_days: 7.5, false_alarm_rate: 0.4, miss_rate: 0.15, cells: 12 },
		...overrides
	};
}

const EAST_BASELINE = { onset_error_days: 5, false_alarm_rate: 0.5, miss_rate: 0.3, cells: 1 };

/** Two models over two Amhara zones and two lead windows, with one zone not covered. */
export function skillByArea(): SkillByArea {
	return {
		areas_from: 'area_of_interest',
		level: 'adm2',
		models: ['aifs', 'fuxi'],
		windows: ['1-15', '16-30'],
		not_shown: [],
		not_covered: ['Wag Hemra'],
		rows: [
			skillRow({
				area: 'East Gojjam',
				cells: 1,
				onset_error_days: 8,
				climatology: EAST_BASELINE
			}),
			skillRow({
				area: 'East Gojjam',
				model: 'fuxi',
				cells: 1,
				onset_error_days: 9,
				climatology: EAST_BASELINE
			}),
			skillRow({}),
			skillRow({
				model: 'fuxi',
				cells: 8,
				onset_error_days: 4.8,
				false_alarm_rate: 0.25,
				miss_rate: 0.2
			}),
			skillRow({ window: '16-30', onset_error_days: 11.1 }),
			skillRow({ window: '16-30', model: 'fuxi', onset_error_days: 10.4 })
		]
	};
}
