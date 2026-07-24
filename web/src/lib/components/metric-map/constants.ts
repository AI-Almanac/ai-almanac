import type { BoundaryLevel, BoundaryStyleDef } from './types';

export { BASEMAP_STYLES, isDarkBasemap, type BasemapStyleId } from '$lib/basemaps';

export const BOUNDARY_LEVELS: Record<BoundaryLevel, BoundaryStyleDef> = {
	adm1: {
		label: 'Admin 1',
		type: 'ADM1',
		strokeColor: 'rgba(25, 35, 52, 0.96)',
		haloColor: 'rgba(255, 255, 255, 0.9)',
		strokeWidth: 2.2,
		haloWidth: 4.6,
		zIndex: 38
	},
	adm2: {
		label: 'Admin 2',
		type: 'ADM2',
		strokeColor: 'rgba(67, 82, 103, 0.78)',
		haloColor: 'rgba(255, 255, 255, 0.72)',
		strokeWidth: 1.5,
		haloWidth: 2.2,
		zIndex: 36
	}
};

export const COLOR_SCALES: Record<string, string[][]> = {
	false_alarm_rate: [
		['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026'],
		['#feebe2', '#fbb4b9', '#f768a1', '#c51b8a', '#7a0177'],
		['#fff5eb', '#fdd0a2', '#fdae6b', '#e6550d', '#a63603'],
		['#f2f0f7', '#cbc9e2', '#9e9ac8', '#756bb1', '#54278f']
	],
	miss_rate: [
		['#eff3ff', '#bdd7e7', '#6baed6', '#2171b5', '#084594'],
		['#edf8fb', '#b2e2e2', '#66c2a4', '#2ca25f', '#006d2c'],
		['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'],
		['#fff7fb', '#ece2f0', '#a6bddb', '#1c9099', '#016450']
	],
	mean_mae: [
		['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'],
		['#fcfbfd', '#dadaeb', '#9e9ac8', '#756bb1', '#54278f'],
		['#fff5f0', '#fdd0a2', '#fc8d59', '#d7301f', '#7f0000'],
		['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b']
	],
	rmse: [
		['#ffffcc', '#c2e699', '#78c679', '#31a354', '#006837'],
		['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'],
		['#fff5f0', '#fdd0a2', '#fc8d59', '#d7301f', '#7f0000'],
		['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b']
	],
	mae: [
		['#ffffcc', '#c2e699', '#78c679', '#31a354', '#006837'],
		['#fff5f0', '#fdd0a2', '#fc8d59', '#d7301f', '#7f0000'],
		['#fff7fb', '#ece2f0', '#a6bddb', '#1c9099', '#016450'],
		['#fcfbfd', '#dadaeb', '#9e9ac8', '#756bb1', '#54278f']
	],
	acc: [
		['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b'],
		['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'],
		['#f7f7f7', '#cccccc', '#969696', '#525252', '#252525'],
		['#f7fcfd', '#ccece6', '#66c2a4', '#238b45', '#005824']
	]
};

export const FALLBACK_SCALE = ['#f7f7f7', '#cccccc', '#969696', '#525252', '#252525'];
export const DIVERGING_STOPS = ['#2166ac', '#92c5de', '#f7f7f7', '#f4a582', '#b2182b'];

export function getStops(metricValue: string, colorIndex: number): string[] {
	if (metricValue === 'bias') return DIVERGING_STOPS;
	const scales = COLOR_SCALES[metricValue];
	if (!scales) return FALLBACK_SCALE;
	return scales[colorIndex % scales.length];
}

export function sharedStops(metricValue: string): string[] {
	return getStops(metricValue, 0);
}
