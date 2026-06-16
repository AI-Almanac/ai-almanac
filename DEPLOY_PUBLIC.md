# Public deployment

This file is retained for existing links. The previous instructions described
an obsolete attribution-only deployment model and must not be used.

For current personal and shared hosting instructions, including the reference
Caddy, oauth2-proxy, PostgreSQL, persistent storage, and GPU Compose stack, see
[`docs/deployment.md`](./docs/deployment.md).

Public or multi-user installations must use `DEPLOYMENT_MODE=shared`. Shared
mode enforces authenticated proxy identity, PostgreSQL, user ownership,
administrator admission, and safer feature defaults.
