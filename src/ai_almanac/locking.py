"""Advisory file locking for concurrent-process coordination.

Used to serialize concurrent `env prepare` calls (pixi install into a shared
env root is not concurrent-safe) and secrets bootstrap writes.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def file_lock(path: Path, message: str | None = None):
    """Exclusive advisory lock on `path` (created if absent).

    Tries a non-blocking acquire first; if the lock is held, logs `message` and
    blocks until it is released. On platforms without `fcntl` (Windows), degrades
    to a no-op with a debug log — concurrent access is the operator's problem.
    """
    try:
        import fcntl
    except ImportError:
        logger.debug("file_lock: fcntl unavailable, skipping lock for %s", path)
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if message:
                logger.info("%s", message)
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
