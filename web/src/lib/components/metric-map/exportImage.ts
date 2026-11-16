/**
 * The map as a picture for a bulletin: the current view under a title in words,
 * with its colour scale and the credits the basemap requires.
 */
import type * as maplibregl from 'maplibre-gl';
import { scaleEnd } from './colourRange';
import { mapFileName } from './exportTitle';
import { isHigherBetterMetric, isNeutralDeltaMetric } from './mapUi';
import type { LayerState } from './types';

export type LegendSwatch = { color: string; label: string };

export type LegendSpec = {
	stops: string[];
	low: string;
	high: string;
	note: string;
	/** Separate colours with a name each, drawn instead of the bar when given. */
	swatches?: LegendSwatch[];
};

const BEYOND_SCALE_NOTE = 'Values beyond the scale take its end colour';

export type MapImageText = {
	title: string;
	subtitle: string;
	legend: LegendSpec | null;
	credit: string;
};

function scaleNumber(value: number, unit: string): string {
	if (unit === 'fraction') return `${Math.round(value * 100)}%`;
	if (unit === 'days') return `${value.toFixed(1)} days`;
	return `${value.toFixed(2)}${unit && unit !== 'dimensionless' ? ` ${unit}` : ''}`;
}

function differenceNote(metric: string): string {
	if (isNeutralDeltaMetric(metric)) return 'Difference';
	return `Difference: ${isHigherBetterMetric(metric) ? 'higher' : 'lower'} is better`;
}

/** The ends of the colour scale as the on-screen legend gives them, in plain units. */
export function legendSpec(
	layer: Pick<LayerState, 'stops' | 'data' | 'isDelta' | 'deltaMaxAbs' | 'clipped'>,
	metric: string
): LegendSpec {
	const { unit } = layer.data;
	if (layer.isDelta && layer.deltaMaxAbs != null) {
		return {
			stops: layer.stops,
			low: scaleEnd(`−${scaleNumber(layer.deltaMaxAbs, unit)}`, 'low', layer.clipped?.low),
			high: scaleEnd(`+${scaleNumber(layer.deltaMaxAbs, unit)}`, 'high', layer.clipped?.high),
			note: differenceNote(metric)
		};
	}
	return {
		stops: layer.stops,
		low: scaleEnd(scaleNumber(layer.data.min, unit), 'low', layer.clipped?.low),
		high: scaleEnd(scaleNumber(layer.data.max, unit), 'high', layer.clipped?.high),
		note: layer.clipped?.low || layer.clipped?.high ? BEYOND_SCALE_NOTE : ''
	};
}

/** How long an export waits for the map to finish drawing, and then for one frame. */
export const CAPTURE_TIMEOUT_MS = 3000;

/** What a capture needs of the map, so the waiting can be tested without one. */
export type CapturableMap = Pick<
	maplibregl.Map,
	'loaded' | 'once' | 'off' | 'triggerRepaint' | 'getCanvas'
>;

/**
 * Resolves once the map has every tile in view, or after the wait: a basemap that
 * is slow to arrive should not hold the picture back for ever.
 */
function settled(map: CapturableMap, timeoutMs: number): Promise<void> {
	if (map.loaded()) return Promise.resolve();
	return new Promise((resolve) => {
		const done = () => {
			clearTimeout(timer);
			map.off('idle', done);
			resolve();
		};
		const timer = setTimeout(done, timeoutMs);
		map.once('idle', done);
	});
}

/** The frame as the map has just drawn it; fails when no frame comes, as in a hidden tab. */
function nextFrame<T>(map: CapturableMap, timeoutMs: number, read: () => T): Promise<T> {
	return new Promise((resolve, reject) => {
		const onRender = () => {
			clearTimeout(timer);
			try {
				resolve(read());
			} catch (e) {
				reject(e);
			}
		};
		const timer = setTimeout(() => {
			map.off('render', onRender);
			reject(new Error('The map did not redraw in time'));
		}, timeoutMs);
		map.once('render', onRender);
		map.triggerRepaint();
	});
}

function copyOf(source: HTMLCanvasElement): HTMLCanvasElement {
	const copy = document.createElement('canvas');
	copy.width = source.width;
	copy.height = source.height;
	copy.getContext('2d')!.drawImage(source, 0, 0);
	return copy;
}

/**
 * The map's pixels as they are drawn, once its tiles are in. A WebGL canvas is
 * cleared once a frame has been shown, so it is copied inside the render event of
 * a frame asked for here.
 */
export async function captureMap<T = HTMLCanvasElement>(
	map: CapturableMap,
	timeoutMs = CAPTURE_TIMEOUT_MS,
	read: (canvas: HTMLCanvasElement) => T = copyOf as (canvas: HTMLCanvasElement) => T
): Promise<T> {
	await settled(map, timeoutMs);
	return nextFrame(map, timeoutMs, () => read(map.getCanvas()));
}

function wrapped(context: CanvasRenderingContext2D, text: string, width: number): string[] {
	const lines: string[] = [];
	let line = '';
	for (const word of text.split(' ')) {
		const next = line ? `${line} ${word}` : word;
		if (line && context.measureText(next).width > width) {
			lines.push(line);
			line = word;
		} else {
			line = next;
		}
	}
	return line ? [...lines, line] : lines;
}

const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';

/** Title above the map, colour scale and credits below, on white so it prints. */
function composeMapImage(mapImage: HTMLCanvasElement, text: MapImageText) {
	// Sizes follow the picture's width, so the text reads the same at any screen density.
	const unit = mapImage.width / 60;
	const margin = unit * 1.2;
	const inner = mapImage.width - margin * 2;
	const measure = document.createElement('canvas').getContext('2d')!;
	const titleFont = `600 ${unit * 1.5}px ${FONT}`;
	const bodyFont = `${unit * 1.1}px ${FONT}`;
	const smallFont = `${unit * 0.85}px ${FONT}`;
	measure.font = titleFont;
	const titleLines = wrapped(measure, text.title, inner);
	measure.font = bodyFont;
	const subtitleLines = wrapped(measure, text.subtitle, inner);
	const headHeight =
		margin + titleLines.length * unit * 1.9 + subtitleLines.length * unit * 1.5 + margin * 0.5;
	const legendHeight = text.legend ? unit * 4.2 : 0;
	const footHeight = margin * 0.6 + legendHeight + unit * 1.4 + margin * 0.6;

	const image = document.createElement('canvas');
	image.width = mapImage.width;
	image.height = Math.round(headHeight + mapImage.height + footHeight);
	const context = image.getContext('2d')!;
	context.fillStyle = '#ffffff';
	context.fillRect(0, 0, image.width, image.height);
	context.textBaseline = 'top';

	let y = margin;
	context.fillStyle = '#1b1b1b';
	context.font = titleFont;
	for (const line of titleLines) {
		context.fillText(line, margin, y);
		y += unit * 1.9;
	}
	context.fillStyle = '#444444';
	context.font = bodyFont;
	for (const line of subtitleLines) {
		context.fillText(line, margin, y);
		y += unit * 1.5;
	}
	context.drawImage(mapImage, 0, Math.round(headHeight));

	y = headHeight + mapImage.height + margin * 0.6;
	if (text.legend?.swatches) {
		context.font = bodyFont;
		let x = margin;
		for (const swatch of text.legend.swatches) {
			context.fillStyle = swatch.color;
			context.fillRect(x, y, unit, unit);
			context.strokeStyle = '#999999';
			context.strokeRect(x, y, unit, unit);
			context.fillStyle = '#333333';
			context.fillText(swatch.label, x + unit * 1.4, y - unit * 0.1);
			x += unit * 1.4 + context.measureText(swatch.label).width + unit * 1.6;
		}
		if (text.legend.note) context.fillText(text.legend.note, margin, y + unit * 1.6);
		y += legendHeight;
	} else if (text.legend) {
		const barWidth = Math.min(inner, unit * 24);
		const gradient = context.createLinearGradient(margin, 0, margin + barWidth, 0);
		text.legend.stops.forEach((stop, i, stops) =>
			gradient.addColorStop(stops.length > 1 ? i / (stops.length - 1) : 0, stop)
		);
		context.fillStyle = gradient;
		context.fillRect(margin, y, barWidth, unit);
		context.strokeStyle = '#999999';
		context.strokeRect(margin, y, barWidth, unit);
		context.fillStyle = '#333333';
		context.font = bodyFont;
		context.fillText(text.legend.low, margin, y + unit * 1.4);
		context.textAlign = 'right';
		context.fillText(text.legend.high, margin + barWidth, y + unit * 1.4);
		context.textAlign = 'left';
		if (text.legend.note) {
			context.fillText(text.legend.note, margin + barWidth + unit, y - unit * 0.1);
		}
		y += legendHeight;
	}
	context.fillStyle = '#777777';
	context.font = smallFont;
	context.fillText(text.credit, margin, y);
	return image;
}

function downloadImage(image: HTMLCanvasElement, fileName: string): Promise<void> {
	return new Promise((resolve, reject) => {
		image.toBlob((blob) => {
			if (!blob) return reject(new Error('The map could not be turned into an image'));
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = fileName;
			link.click();
			// Revoked later: some browsers start the download after this task ends.
			setTimeout(() => URL.revokeObjectURL(url), 60_000);
			resolve();
		}, 'image/png');
	});
}

/** Download the map as it is drawn now, under its title, colour scale and credits. */
export async function exportMapImage(map: CapturableMap, text: MapImageText): Promise<void> {
	await downloadImage(composeMapImage(await captureMap(map), text), mapFileName(text));
}
