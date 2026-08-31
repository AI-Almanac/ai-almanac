// ---- Account / identity ------------------------------------------------------
import { request } from './core';

export type Account = {
	// True for a visitor with no credential; identity fields are null then.
	anonymous?: boolean;
	id: string | null;
	subject: string | null;
	email: string | null;
	display_name: string | null;
	role: 'admin' | 'user';
	deployment_mode: 'personal' | 'shared';
	capabilities: {
		can_admin: boolean;
		can_browse_fs: boolean;
		can_manage_data: boolean;
		can_use_forecasting: boolean;
		can_run_code: boolean;
	};
};

export async function getAccount(): Promise<Account> {
	return request<Account>('/auth/me');
}
