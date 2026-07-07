// The API client, split by feature domain. Everything re-exports through here
// so consumers keep importing from `$lib/api`. The shared fetch wrapper and
// runtime base-URL logic live in `core.ts`; types bound to the generated
// OpenAPI schema (`../api-types.gen.ts`) are the pattern to follow — see
// `data-sources.ts`.
export * from './core';
export * from './fs';
export * from './settings';
export * from './data-sources';
export * from './config';
export * from './llm';
export * from './account';
export * from './regions';
export * from './datasets';
export * from './jobs';
export * from './blends';
export * from './forecasts';
export * from './chat';
