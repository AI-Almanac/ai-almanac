import {
	getConfigYamlPath,
	getSettings,
	getSettingsSchema,
	patchSettings,
	type SettingsField,
	type SettingsGroup
} from '$lib/api';

/**
 * The platform configuration form: schema, current values, and per-group saves.
 *
 * Lives outside the route components because the settings shell needs the group
 * list for its side nav while each section page edits one group — one fetch,
 * one source of truth for what is dirty.
 */
export class ConfigSettingsState {
	groups = $state<SettingsGroup[]>([]);
	deploymentMode = $state('personal');
	configPath = $state('');
	loading = $state(true);
	error = $state<string | null>(null);
	savingGroup = $state<string | null>(null);
	savedFlash = $state<string | null>(null);

	private values = $state<Record<string, unknown>>({});
	private original = $state<Record<string, unknown>>({});
	/** Whether a secret has a value stored server-side. The plaintext never
	 * reaches the client, so this is all we can know. */
	configured = $state<Record<string, boolean>>({});

	readonly isShared = $derived(this.deploymentMode === 'shared');

	value(name: string): unknown {
		return this.values[name];
	}

	isDirty(name: string): boolean {
		return this.values[name] !== this.original[name];
	}

	dirtyInGroup(group: SettingsGroup): boolean {
		return group.fields.some((field) => this.isDirty(field.name));
	}

	restartFieldsEdited(group: SettingsGroup): SettingsField[] {
		return group.fields.filter((field) => field.restart_required && this.isDirty(field.name));
	}

	async load() {
		this.loading = true;
		this.error = null;
		try {
			const [schema, state, path] = await Promise.all([
				getSettingsSchema(),
				getSettings(),
				getConfigYamlPath()
			]);
			this.groups = schema.groups;
			this.deploymentMode = schema.deployment_mode;
			this.values = { ...state.values };
			this.original = { ...state.values };
			// Secret inputs start blank and only ever hold a typed replacement.
			this.configured = { ...state.secrets };
			for (const name of Object.keys(this.configured)) {
				this.values[name] = '';
				this.original[name] = '';
			}
			// A multiline field with no override shows the effective default, so
			// the box never looks empty. Starts clean until edited.
			for (const group of schema.groups) {
				for (const field of group.fields) {
					if (field.multiline && !this.values[field.name]) {
						this.values[field.name] = field.default;
						this.original[field.name] = field.default;
					}
				}
			}
			this.configPath = path;
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		} finally {
			this.loading = false;
		}
	}

	setValue(field: SettingsField, raw: string | boolean) {
		if (field.type === 'bool') {
			this.values[field.name] = raw;
			return;
		}
		if (field.type === 'int' || field.type === 'float') {
			const parsed = field.type === 'int' ? parseInt(String(raw), 10) : parseFloat(String(raw));
			this.values[field.name] = Number.isNaN(parsed) ? raw : parsed;
			return;
		}
		this.values[field.name] = raw;
	}

	async saveGroup(group: SettingsGroup) {
		this.savingGroup = group.name;
		this.error = null;
		try {
			const patch: Record<string, unknown> = {};
			for (const field of group.fields) {
				if (this.isDirty(field.name)) patch[field.name] = this.values[field.name];
			}
			if (Object.keys(patch).length === 0) return;
			const updated = await patchSettings(patch);
			for (const name of Object.keys(patch)) {
				if (name in updated.secrets) {
					this.configured[name] = updated.secrets[name];
					this.values[name] = '';
					this.original[name] = '';
				} else {
					this.values[name] = updated.values[name];
					this.original[name] = updated.values[name];
				}
			}
			this.flash(`${group.name} saved`);
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		} finally {
			this.savingGroup = null;
		}
	}

	async removeKey(field: SettingsField) {
		this.error = null;
		try {
			const updated = await patchSettings({ [field.name]: '' });
			this.configured[field.name] = updated.secrets[field.name] ?? false;
			this.values[field.name] = '';
			this.original[field.name] = '';
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		}
	}

	private flash(message: string) {
		this.savedFlash = message;
		setTimeout(() => {
			if (this.savedFlash === message) this.savedFlash = null;
		}, 2200);
	}
}

export function inputType(field: SettingsField): string {
	if (field.type === 'int' || field.type === 'float') return 'number';
	if (field.sensitive) return 'password';
	return 'text';
}
