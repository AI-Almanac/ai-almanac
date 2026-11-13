import { describe, expect, it } from 'vitest';

import type { Country } from '../src/lib/api/places';
import type { Region } from '../src/lib/api/regions';
import { pickedPlaces, regionCountry, toggled, wholeCountry } from '../src/lib/places';

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
