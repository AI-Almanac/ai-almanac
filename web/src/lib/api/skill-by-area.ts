// ---- A benchmark run's skill by administrative area ---------------------------
import type { components } from '../api-types.gen';
import { request } from './core';

export type SkillByArea = components['schemas']['SkillByArea'];
export type AreaSkillRow = components['schemas']['AreaSkillRow'];
export type AreaSkill = components['schemas']['AreaSkill'];
export type AreaLevel = SkillByArea['level'];

/**
 * `runKey` is the id the run's jobs share, or the job's own id when it ran alone.
 * With no `level` the areas are the ones the run itself was scored on.
 */
export async function getSkillByArea(
	workspaceId: string,
	runKey: string,
	options: { level?: AreaLevel } = {}
): Promise<SkillByArea> {
	const query = options.level ? `?level=${options.level}` : '';
	return request<SkillByArea>(
		`/workspaces/${workspaceId}/runs/${encodeURIComponent(runKey)}/skill-by-area${query}`
	);
}
