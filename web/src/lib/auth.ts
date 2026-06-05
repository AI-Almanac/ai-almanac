// ai-almanac has no built-in authentication. This module is a no-op shim that
// preserves the old `getManager()` API so call sites compile unchanged. Public
// deployments authenticate at a reverse proxy (oauth2-proxy / Caddy with OIDC
// / Cloudflare Access) which forwards the user identity via the
// `X-Forwarded-User` header. The backend reads that header and records it as
// `submitted_by` on jobs/datasets, for attribution only.

export interface AuthShim {
	authenticated: boolean;
	user: { name: string; email: string | null };
	login(): void;
	revoke(): Promise<void>;
}

const shim: AuthShim = {
	authenticated: true,
	user: { name: 'You', email: null },
	login() {},
	async revoke() {}
};

export function getManager(): AuthShim {
	return shim;
}
