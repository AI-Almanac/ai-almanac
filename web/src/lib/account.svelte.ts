// Account state, loaded once from `/auth/me`. Drives admin-only navigation and
// actions. In personal installs the implicit operator is an admin, so gated UI
// stays visible; in shared deployments non-admins see a reduced surface.

import { getAccount, type Account } from './api';

class AccountState {
	account = $state<Account | null>(null);
	loaded = $state(false);
	loading = $state(false);

	/**
	 * An admin previewing the interface a regular user gets.
	 *
	 * Only `isAdmin` is downgraded — every admin-gated view in the app reads
	 * that one flag. It hides admin UI in this browser and changes nothing
	 * server-side, so it previews the interface, not the permissions: an
	 * admin-only request made while previewing still succeeds.
	 *
	 * Deliberately not persisted. Stored state survived reloads and could strand
	 * an admin with no admin UI — and with none at all if `/auth/me` then failed,
	 * because the banner carrying the way out needs a loaded account. In memory,
	 * a reload is always the escape hatch, and SPA navigation still holds it.
	 */
	viewingAsUser = $state(false);

	get isAdmin(): boolean {
		return this.isActuallyAdmin && !this.viewingAsUser;
	}

	/** The real role, for the preview toggle itself and its banner. */
	get isActuallyAdmin(): boolean {
		return this.account?.capabilities.can_admin ?? false;
	}

	setViewingAsUser(viewing: boolean): void {
		this.viewingAsUser = viewing;
	}

	get isAnonymous(): boolean {
		return this.account?.anonymous ?? false;
	}

	get isShared(): boolean {
		return this.account?.deployment_mode === 'shared';
	}

	get canBrowseFs(): boolean {
		return this.account?.capabilities.can_browse_fs ?? false;
	}

	get canManageData(): boolean {
		return this.account?.capabilities.can_manage_data ?? false;
	}

	get canUseForecasting(): boolean {
		return this.account?.capabilities.can_use_forecasting ?? false;
	}

	get label(): string {
		const a = this.account;
		if (!a) return '';
		return a.display_name || a.email || a.subject || '';
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
