import { describe, expect, it } from 'vitest';

import type { Country } from '../src/lib/api/places';
import type { Region } from '../src/lib/api/regions';
import {
	afterToggling,
	countryNamed,
	countryOfSelection,
	matchingNames,
	pickedAt,
	pickedPlaces,
	regionCountry,
	toggled,
	wholeCountry
} from '../src/lib/places';

const KENYA: Country = { iso: 'KEN', name: 'Kenya' };

describe('picking places on the map', () => {
	it('names the country with the places, so the run is scored there', () => {
		expect(pickedPlaces('adm1', ['Turkana', 'Marsabit'], 'KEN')).toEqual({
			level: 'adm1',
			units: ['Turkana', 'Marsabit'],
			iso: 'KEN'
		});
	});

	it("leaves the country out when none is known, so the run's region supplies it", () => {
		expect(pickedPlaces('adm2', ['Awi'], null)).toEqual({ level: 'adm2', units: ['Awi'] });
	});

	it('is no selection at all once the last place is removed', () => {
		expect(pickedPlaces('adm1', toggled(['Turkana'], 'Turkana'), 'KEN')).toBeNull();
	});

	it('adds a place that was not picked and removes one that was', () => {
		expect(toggled(['Turkana'], 'Marsabit')).toEqual(['Turkana', 'Marsabit']);
		expect(toggled(['Turkana', 'Marsabit'], 'Turkana')).toEqual(['Marsabit']);
	});

	it('treats a whole country as one place named for it', () => {
		expect(wholeCountry(KENYA)).toEqual({ level: 'adm0', units: ['Kenya'], iso: 'KEN' });
	});
});

describe('where places are picked by default', () => {
	const regions = [
		{ id: 'ethiopia', boundary_iso: 'ETH' },
		{ id: 'drawn-box', boundary_iso: null }
	] as unknown as Region[];

	it("is the country the run's region is", () => {
		expect(regionCountry(regions, 'ethiopia')).toBe('ETH');
	});

	it('is nowhere for a region that is not a country, or for no region', () => {
		expect(regionCountry(regions, 'drawn-box')).toBeNull();
		expect(regionCountry(regions, null)).toBeNull();
	});
});

describe('narrowing a country down to places', () => {
	const kenyaWhole = { level: 'adm0' as const, units: ['Kenya'], iso: 'KEN' };

	it('starts from the whole country, so the first place picked replaces it', () => {
		expect(afterToggling(kenyaWhole, 'adm1', 'Turkana', KENYA)).toEqual({
			level: 'adm1',
			units: ['Turkana'],
			iso: 'KEN'
		});
	});

	it('goes back to the whole country once the last place is removed', () => {
		const turkana = { level: 'adm1' as const, units: ['Turkana'], iso: 'KEN' };
		expect(afterToggling(turkana, 'adm1', 'Turkana', KENYA)).toEqual(kenyaWhole);
	});

	it('counts no places picked while the selection is the whole country', () => {
		expect(pickedAt(kenyaWhole, 'adm0')).toEqual([]);
		expect(pickedAt(kenyaWhole, 'adm1')).toEqual([]);
	});
});

describe('finding a country or place by name', () => {
	const countries: Country[] = [KENYA, { iso: 'ETH', name: 'Ethiopia' }];

	it('takes a country typed in any case', () => {
		expect(countryNamed(countries, '  ethiopia ')).toEqual({ iso: 'ETH', name: 'Ethiopia' });
	});

	it('waits for a full country name rather than guessing from part of one', () => {
		expect(countryNamed(countries, 'Eth')).toBeNull();
	});

	it('lists the places that mention every word typed, alphabetically', () => {
		expect(matchingNames(['West Gojjam', 'Awi', 'East Gojjam'], 'gojjam')).toEqual([
			'East Gojjam',
			'West Gojjam'
		]);
	});
});

describe('the country a selection names', () => {
	it('is the whole country a selection is, without needing the country list', () => {
		expect(countryOfSelection({ level: 'adm0', units: ['Kenya'], iso: 'KEN' })).toEqual(KENYA);
	});

	it('is not guessed from places or a box', () => {
		expect(countryOfSelection({ level: 'adm1', units: ['Turkana'], iso: 'KEN' })).toBeNull();
		expect(countryOfSelection({ lat_min: 1, lat_max: 2, lon_min: 3, lon_max: 4 })).toBeNull();
	});
});
