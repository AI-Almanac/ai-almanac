"""Job runner selection.

`get_job_runner()` returns the runner for new submissions, chosen by the
`job_runner` setting. The reconciler resolves a remote runner directly (it
already knows the handle's backend), so there is no name-dispatch layer here.
"""

from __future__ import annotations

from ai_almanac.settings import settings


def get_job_runner():
    if settings.job_runner == "modal":
        from ai_almanac.server.services.modal_runner import get_modal_runner

        return get_modal_runner()
    from ai_almanac.server.services.local_runner import get_job_runner as _local

    return _local()
