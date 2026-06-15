"""Job runner selection.

`get_job_runner()` returns the runner for new submissions (chosen by the
`job_runner` setting). `runner_for(name)` returns the runner that owns an
existing job's persisted handle, so the reconciler can inspect/cancel a job
regardless of which backend submitted it.
"""

from __future__ import annotations

from ai_almanac.settings import settings


def runner_for(name: str):
    if name == "modal":
        from ai_almanac.server.services.modal_runner import get_modal_runner

        return get_modal_runner()
    from ai_almanac.server.services.local_runner import get_job_runner as _local

    return _local()


def get_job_runner():
    return runner_for(settings.job_runner)
