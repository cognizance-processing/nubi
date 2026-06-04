"""Scheduled jobs package — M11-A.

Public API
----------
store
    :class:`~app.jobs.store.InMemoryJobStore` and
    :func:`~app.jobs.store.get_job_store` — in-memory job/run storage.
schedule
    :func:`~app.jobs.schedule.next_run` and
    :func:`~app.jobs.schedule.run_due_jobs` — scheduling logic (deterministic;
    ``now`` is always a parameter — no hidden ``datetime.now()`` inside core).
executor
    :func:`~app.jobs.executor.execute_job` — run a single job synchronously
    and return a job_run dict.
"""
