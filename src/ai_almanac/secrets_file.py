"""Read the auto-generated secrets.env layer for personal-mode installs.

Kept apart from secrets_bootstrap, which generates the file and needs the
resolved settings: settings itself loads this layer, so reading it must not
import settings.
"""

from __future__ import annotations

import logging
import stat

from ai_almanac.paths import secrets_env_path

logger = logging.getLogger(__name__)

# Maps the env-var name (upper) → settings field name (lower).
SECRETS_ENV_NAMES: dict[str, str] = {
    "CREDENTIAL_ENCRYPTION_KEY": "credential_encryption_key",
    "CHAT_FIGURE_SIGNING_SECRET": "chat_figure_signing_secret",
}


def load_secrets_file() -> dict[str, str]:
    """Parse secrets.env and return managed keys only, keyed by settings field name.

    Missing or unreadable file → {}. Group/other-readable file → warn and chmod 0600.
    """
    path = secrets_env_path()
    if not path.exists():
        return {}

    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.warning(
                "secrets.env at %s is group/other-readable; tightening permissions to 0600",
                path,
            )
            path.chmod(0o600)
    except OSError:
        logger.warning("could not check or tighten permissions on %s", path)

    try:
        lines = path.read_text().splitlines()
    except OSError:
        logger.warning("could not read %s; generated secrets are not loaded", path)
        return {}

    result: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        field = SECRETS_ENV_NAMES.get(key.strip())
        if field:
            result[field] = value.strip()
    return result
