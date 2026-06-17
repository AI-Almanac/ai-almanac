// Account state, loaded once from `/auth/me`. Drives admin-only navigation and
// actions. In personal installs the implicit operator is an admin, so gated UI
// stays visible; in shared deployments non-admins see a reduced surface.

import { getAccount, type Account } from './api';

class AccountState {
	account = $state<Account | null>(null);
	loaded = $state(false);
	loading = $state(false);

	get isAdmin(): boolean {
		return this.account?.capabilities.can_admin ?? false;
	}

	get isShared(): boolean {
		return this.account?.deployment_mode === 'shared';
	}

	get canBrowseFs(): boolean {
		return this.account?.capabilities.can_browse_fs ?? false;
	}

	get label(): string {
		const a = this.account;
		if (!a) return '';
		return a.display_name || a.email || a.subject;
	}

	async load(): Promise<void> {
		if (this.loading) return;
		this.loading = true;
		try {
			this.account = await getAccount();
		} catch {
			this.account = null;
		} finally {
			this.loaded = true;
			this.loading = false;
		}
	}

	async reload(): Promise<void> {
		this.loaded = false;
		await this.load();
	}

	clear(): void {
		this.account = null;
		this.loaded = true;
	}
}

export const account = new AccountState();
