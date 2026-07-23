export type AlmanacReference = {
	label: string;
	href: string;
};

export type ModelFamily = {
	slug: string;
	name: string;
	modelType: 'AI weather prediction' | 'Numerical weather prediction' | 'Hybrid';
	summary: string;
	checkpoints: string[];
	trainingDatasets: string[];
	validationDatasets: string[];
	resolution: string;
	forecastRange: string;
	cadence: string;
	variables: string[];
	notes: string[];
	references: AlmanacReference[];
};

export type WeatherDataset = {
	slug: string;
	name: string;
	role: 'Training' | 'Validation' | 'Benchmarking' | 'Observation';
	summary: string;
	source: string;
	coverage: string;
	resolution: string;
	variables: string[];
	usedFor: string[];
	notes: string[];
	references: AlmanacReference[];
};

export type WeatherArchitecture = {
	slug: string;
	name: string;
	summary: string;
	modelFamilies: string[];
	keyIdeas: string[];
	notes: string[];
	references: AlmanacReference[];
};

export const modelFamilies: ModelFamily[] = [
	{
		slug: 'aifs',
		name: 'AIFS',
		modelType: 'AI weather prediction',
		summary: 'ECMWF AI Forecasting System family.',
		checkpoints: ['aifs', 'aifsdaily'],
		trainingDatasets: ['ERA5', 'IFS analyses'],
		validationDatasets: ['ERA5', 'Operational forecast archives'],
		resolution: '0.25 degrees',
		forecastRange: 'Medium range',
		cadence: 'Daily and twice-weekly variants are represented in the benchmark catalog.',
		variables: ['Precipitation'],
		notes: ['Add details about released checkpoints, initialization schedules, and data lineage.'],
		references: []
	},
	{
		slug: 'graphcast',
		name: 'GraphCast',
		modelType: 'AI weather prediction',
		summary: 'Graph neural network weather model family from Google DeepMind.',
		checkpoints: ['graphcast'],
		trainingDatasets: ['ERA5'],
		validationDatasets: ['ERA5'],
		resolution: '0.25 degrees',
		forecastRange: 'Medium range',
		cadence: 'Twice-weekly hindcast initializations in the current benchmark data.',
		variables: ['Precipitation'],
		notes: ['Fill in architecture, training period, and known benchmark caveats.'],
		references: []
	},
	{
		slug: 'fuxi',
		name: 'FuXi',
		modelType: 'AI weather prediction',
		summary: 'Fudan University AI weather prediction model family.',
		checkpoints: ['fuxi', 'fuxis2s'],
		trainingDatasets: ['ERA5'],
		validationDatasets: ['ERA5'],
		resolution: '0.25 degrees and 1.5 degrees variants',
		forecastRange: 'Medium range and subseasonal variants',
		cadence: 'Twice-weekly hindcast initializations in the current benchmark data.',
		variables: ['Precipitation'],
		notes: ['Separate medium-range and subseasonal checkpoints once content is filled in.'],
		references: []
	},
	{
		slug: 'gencast',
		name: 'GenCast',
		modelType: 'AI weather prediction',
		summary: 'Generative ensemble weather model family from Google DeepMind.',
		checkpoints: ['gencast'],
		trainingDatasets: ['ERA5'],
		validationDatasets: ['ERA5'],
		resolution: '0.25 degrees',
		forecastRange: 'Medium range',
		cadence: 'Twice-weekly hindcast initializations in the current benchmark data.',
		variables: ['Precipitation'],
		notes: ['Add ensemble details, training period, and hindcast availability caveats.'],
		references: []
	},
	{
		slug: 'neural-gcm',
		name: 'NeuralGCM',
		modelType: 'Hybrid',
		summary: 'Hybrid dynamical and machine-learning global weather model family.',
		checkpoints: ['ngcm'],
		trainingDatasets: ['ERA5'],
		validationDatasets: ['ERA5'],
		resolution: '2.8 degrees',
		forecastRange: 'Seasonal window represented in current benchmark data',
		cadence: 'Twice-weekly hindcast initializations in the current benchmark data.',
		variables: ['Precipitation'],
		notes: ['Add details about physics coupling, season coverage, and benchmark limits.'],
		references: []
	}
];

export const datasets: WeatherDataset[] = [
	{
		slug: 'era5',
		name: 'ERA5',
		role: 'Training',
		summary:
			'Global atmospheric reanalysis commonly used for weather-model training and validation.',
		source: 'ECMWF Copernicus Climate Data Store',
		coverage: 'Global',
		resolution: '0.25 degrees hourly source data; benchmark products may be aggregated.',
		variables: ['Precipitation', 'Atmospheric state variables'],
		usedFor: ['Training', 'Validation', 'Benchmark observations'],
		notes: ['Document the exact variable names and transformations used by this project.'],
		references: []
	},
	{
		slug: 'chirps',
		name: 'CHIRPS',
		role: 'Observation',
		summary: 'Satellite and station blended precipitation dataset.',
		source: 'Climate Hazards Center',
		coverage: 'Quasi-global land regions',
		resolution: 'To be filled',
		variables: ['Precipitation'],
		usedFor: ['Benchmark observations'],
		notes: ['Add supported regions, temporal coverage, and preprocessing steps.'],
		references: []
	},
	{
		slug: 'imerg',
		name: 'IMERG',
		role: 'Observation',
		summary: 'Global precipitation estimates from the GPM mission.',
		source: 'NASA',
		coverage: 'Global',
		resolution: 'To be filled',
		variables: ['Precipitation'],
		usedFor: ['Benchmark observations'],
		notes: ['Add product version, latency, and aggregation details.'],
		references: []
	}
];

export const architectures: WeatherArchitecture[] = [
	{
		slug: 'graph-neural-network',
		name: 'Graph Neural Network',
		summary: 'Represents atmospheric fields on graph structures to model spatial interactions.',
		modelFamilies: ['GraphCast'],
		keyIdeas: ['Message passing', 'Learned spatial relationships'],
		notes: ['Add diagrams or implementation-neutral descriptions later.'],
		references: []
	},
	{
		slug: 'transformer',
		name: 'Transformer',
		summary: 'Uses attention-based sequence and field modeling for weather prediction.',
		modelFamilies: ['AIFS', 'FuXi'],
		keyIdeas: ['Attention', 'Autoregressive rollout'],
		notes: [
			'Fill in model-specific distinctions rather than treating all transformers as identical.'
		],
		references: []
	},
	{
		slug: 'generative-ensemble',
		name: 'Generative Ensemble',
		summary: 'Models forecast uncertainty by sampling multiple plausible forecast trajectories.',
		modelFamilies: ['GenCast'],
		keyIdeas: ['Probabilistic forecasting', 'Ensemble generation'],
		notes: ['Add validation metrics and uncertainty calibration notes.'],
		references: []
	},
	{
		slug: 'hybrid-dynamical-ml',
		name: 'Hybrid Dynamical ML',
		summary: 'Combines machine-learned components with dynamical-system structure.',
		modelFamilies: ['NeuralGCM'],
		keyIdeas: ['Differentiable dynamics', 'Learned parameterization'],
		notes: ['Clarify which components are learned for each model family.'],
		references: []
	}
];
