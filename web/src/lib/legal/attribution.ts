/**
 * Attribution for the ground-truth observations and forecast models shipped as
 * AI Almanac defaults.
 *
 * License strings describe the terms the upstream provider publishes for the
 * artifact this platform actually consumes — model weights or forecast/observation
 * data, not the surrounding research code, which is often licensed separately.
 * Several entries are non-commercial: keep that visible rather than flattening
 * everything to "open".
 */

export type AttributionLink = {
	label: string;
	href: string;
};

export type AttributionEntry = {
	slug: string;
	/** Name as it appears elsewhere in the app. */
	name: string;
	provider: string;
	license: string;
	/** How AI Almanac uses this source. */
	usage: string;
	citations: string[];
	links: AttributionLink[];
};

export const groundTruthSources: AttributionEntry[] = [
	{
		slug: 'chirps-v3-daily-imerg',
		name: 'CHIRPS v3 daily (IMERG)',
		provider: 'Climate Hazards Center, UC Santa Barbara',
		license:
			'Freely available for public use with attribution (Climate Hazards Center data policy)',
		usage:
			'Primary ground-truth precipitation for benchmark scoring and rainy-season onset detection. The daily product partitions pentadal CHIRPS v3 totals using NASA IMERG Late V07 daily precipitation.',
		citations: [
			'Funk, C., Peterson, P., Harrison, L. et al. The Climate Hazards Center Infrared Precipitation with Stations, Version 3. Scientific Data 13, 718 (2026).'
		],
		links: [
			{ label: 'CHIRPS v3 dataset', href: 'https://www.chc.ucsb.edu/data/chirps3' },
			{ label: 'Paper', href: 'https://doi.org/10.1038/s41597-026-07096-4' },
			{ label: 'Data repository', href: 'https://doi.org/10.15780/G2JQ0P' }
		]
	},
	{
		slug: 'imerg',
		name: 'IMERG',
		provider: 'NASA Goddard Space Flight Center, GPM mission (GES DISC)',
		license: 'Free and open use with citation (NASA EOSDIS data use and citation guidance)',
		usage:
			'Ground-truth precipitation for benchmark scoring, and the observational target that the NeuralGCM precipitation checkpoints are trained against.',
		citations: [
			'Huffman, G. J., Stocker, E. F., Bolvin, D. T., Nelkin, E. J. & Tan, J. GPM IMERG Final Precipitation L3 1 day 0.1 degree x 0.1 degree V07. Goddard Earth Sciences Data and Information Services Center (2023).'
		],
		links: [
			{ label: 'GPM mission data', href: 'https://gpm.nasa.gov/data' },
			{ label: 'Dataset DOI', href: 'https://doi.org/10.5067/GPM/IMERGDF/DAY/07' }
		]
	}
];

export const forecastModelSources: AttributionEntry[] = [
	{
		slug: 'aifs-single-v1p1',
		name: 'AIFS-single-v1p1',
		provider: 'ECMWF',
		license: 'Model weights CC BY 4.0; forecast output CC BY 4.0 under the ECMWF Terms of Use',
		usage: 'Deterministic AI forecast model evaluated for precipitation and onset skill.',
		citations: [
			"Lang, S. et al. AIFS Single 1.1.0: an update to ECMWF's machine-learned weather forecast model AIFS. Geoscientific Model Development 19, 4703 (2026)."
		],
		links: [
			{ label: 'Model weights', href: 'https://huggingface.co/ecmwf/aifs-single-1.1' },
			{ label: 'Paper', href: 'https://gmd.copernicus.org/articles/19/4703/2026/' }
		]
	},
	{
		slug: 'aifs-ens-v1',
		name: 'AIFS-ENS-v1',
		provider: 'ECMWF',
		license: 'Model weights CC BY 4.0; forecast output CC BY 4.0 under the ECMWF Terms of Use',
		usage: 'Ensemble AI forecast model; ensemble members and derived means are scored here.',
		citations: [
			'Lang, S. et al. AIFS-CRPS: Ensemble forecasting using a model trained with a loss function based on the Continuous Ranked Probability Score. arXiv:2412.15832 (2024).'
		],
		links: [
			{ label: 'Model weights', href: 'https://huggingface.co/ecmwf/aifs-ens-1.0' },
			{ label: 'Paper', href: 'https://arxiv.org/abs/2412.15832' }
		]
	},
	{
		slug: 'aifs-single-v2',
		name: 'AIFS-single-v2',
		provider: 'ECMWF',
		license: 'Model weights CC BY 4.0; forecast output CC BY 4.0 under the ECMWF Terms of Use',
		usage: 'Deterministic AI forecast model; ECMWF-operational successor to AIFS Single v1.1.',
		citations: [
			'ECMWF. AIFS Single v2, operationally implemented 12 May 2026, superseding AIFS Single v1.1.'
		],
		links: [
			{ label: 'Model weights', href: 'https://huggingface.co/ecmwf/aifs-single-2.0' },
			{
				label: 'Implementation notes',
				href: 'https://confluence.ecmwf.int/spaces/FCST/pages/620418870/Implementation+of+AIFS+Single+v2'
			}
		]
	},
	{
		slug: 'aifs-ens-v2',
		name: 'AIFS-ENS-v2',
		provider: 'ECMWF',
		license: 'Model weights CC BY 4.0; forecast output CC BY 4.0 under the ECMWF Terms of Use',
		usage: 'Ensemble AI forecast model; ECMWF-operational successor to AIFS ENS v1.',
		citations: [
			'ECMWF. AIFS ENS v2, operationally implemented 12 May 2026, superseding AIFS ENS v1.'
		],
		links: [
			{ label: 'Model weights', href: 'https://huggingface.co/ecmwf/aifs-ens-2.0' },
			{
				label: 'Implementation notes',
				href: 'https://confluence.ecmwf.int/display/FCST/Implementation+of+AIFS+ENS+v2'
			}
		]
	},
	{
		slug: 'fuxi-s2s',
		name: 'FuXi-S2S',
		provider: 'Fudan University',
		license:
			'Research use only; commercial use and use in competitions require permission from the authors',
		usage:
			'Subseasonal-to-seasonal ensemble model used for extended-range precipitation benchmarks.',
		citations: [
			'Chen, L. et al. A machine learning model that outperforms conventional global subseasonal forecast models. Nature Communications 15, 6425 (2024).'
		],
		links: [
			{ label: 'Paper', href: 'https://doi.org/10.1038/s41467-024-50714-1' },
			{ label: 'Code', href: 'https://github.com/tpys/FuXi-S2S' },
			{ label: 'Model and sample data', href: 'https://zenodo.org/records/15718402' }
		]
	},
	{
		slug: 'gencast',
		name: 'GenCast',
		provider: 'Google DeepMind',
		license: 'Code Apache-2.0; model weights CC BY-NC-SA 4.0 (non-commercial)',
		usage:
			'Generative ensemble model used for probabilistic medium-range precipitation benchmarks.',
		citations: [
			'Price, I. et al. Probabilistic weather forecasting with machine learning. Nature 637, 84-90 (2025).'
		],
		links: [
			{ label: 'Paper', href: 'https://doi.org/10.1038/s41586-024-08252-9' },
			{ label: 'Code and weights', href: 'https://github.com/google-deepmind/graphcast' }
		]
	},
	{
		slug: 'graphcast',
		name: 'GraphCast',
		provider: 'Google DeepMind',
		license: 'Code Apache-2.0; model weights CC BY-NC-SA 4.0 (non-commercial)',
		usage:
			'Deterministic graph neural network model used for medium-range precipitation benchmarks.',
		citations: [
			'Lam, R. et al. Learning skillful medium-range global weather forecasting. Science 382, 1416-1421 (2023).'
		],
		links: [
			{ label: 'Paper', href: 'https://doi.org/10.1126/science.adi2336' },
			{ label: 'Code and weights', href: 'https://github.com/google-deepmind/graphcast' }
		]
	},
	{
		slug: 'ifs-s2s-tigge',
		name: 'IFS-S2S (TIGGE)',
		provider: 'ECMWF, via the S2S Prediction Project database and the TIGGE archive',
		license:
			'S2S database: CC BY-NC 4.0, for non-commercial research and education. TIGGE: CC BY 4.0 or CC BY-NC 4.0 depending on the contributing centre; ECMWF TIGGE data are CC BY 4.0.',
		usage:
			'Physics-based numerical weather prediction reforecast baseline that the AI models are compared against.',
		citations: [
			'Vitart, F. et al. The Subseasonal to Seasonal (S2S) Prediction Project Database. Bulletin of the American Meteorological Society 98, 163-173 (2017).',
			'Bougeault, P. et al. The THORPEX Interactive Grand Global Ensemble. Bulletin of the American Meteorological Society 91, 1059-1072 (2010).'
		],
		links: [
			{ label: 'S2S database licence', href: 'https://apps.ecmwf.int/datasets/licences/s2s/' },
			{ label: 'TIGGE licence', href: 'https://apps.ecmwf.int/datasets/data/tigge/licence/' },
			{ label: 'S2S paper', href: 'https://doi.org/10.1175/BAMS-D-16-0017.1' }
		]
	},
	{
		slug: 'neuralgcm-imerg-precip',
		name: 'NeuralGCM (IMERG, precip)',
		provider: 'Google Research',
		license: 'Code Apache-2.0; model checkpoints CC BY-SA 4.0',
		usage:
			'Hybrid physics and machine-learning model. The variant benchmarked here is the precipitation checkpoint trained against IMERG observations.',
		citations: [
			'Kochkov, D. et al. Neural general circulation models for weather and climate. Nature 632, 1060-1066 (2024).',
			'Yuval, J., Langmore, I., Kochkov, D. & Hoyer, S. Neural general circulation models for modeling precipitation. Science Advances (2026).'
		],
		links: [
			{ label: 'Code', href: 'https://github.com/neuralgcm/neuralgcm' },
			{ label: 'Precipitation checkpoints', href: 'https://zenodo.org/records/17109230' },
			{ label: 'Precipitation paper', href: 'https://doi.org/10.1126/sciadv.adv6891' }
		]
	},
	{
		slug: 'fuxi-deterministic',
		name: 'FuXi (deterministic)',
		provider: 'Fudan University',
		license: 'Model weights CC BY-NC-SA 4.0; commercial use prohibited',
		usage: 'Deterministic medium-range model used for precipitation and onset benchmarks.',
		citations: [
			'Chen, L. et al. FuXi: a cascade machine learning forecasting system for 15-day global weather forecast. npj Climate and Atmospheric Science 6, 190 (2023).'
		],
		links: [
			{ label: 'Paper', href: 'https://doi.org/10.1038/s41612-023-00512-1' },
			{ label: 'Code and weights', href: 'https://github.com/tpys/FuXi' }
		]
	}
];

export type AttributionSection = {
	slug: string;
	title: string;
	description: string;
	entries: AttributionEntry[];
};

export const attributionSections: AttributionSection[] = [
	{
		slug: 'ground-truth',
		title: 'Ground truth',
		description: 'Observational precipitation datasets that benchmark scores are computed against.',
		entries: groundTruthSources
	},
	{
		slug: 'models',
		title: 'Forecast models',
		description: 'Forecast models whose output is distributed or reproduced by this platform.',
		entries: forecastModelSources
	}
];
