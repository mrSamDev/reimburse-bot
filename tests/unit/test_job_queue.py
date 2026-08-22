"""Tests for the in-process JobQueue drained by background workers."""

import asyncio

import pytest

from app.bot.queue import Job, JobQueue, QueueFullError


async def test_enqueue_returns_position():
    q = JobQueue(worker_count=1)
    assert q.enqueue(Job(user_id=1, chat_id=1, file_ids=["f1"])) == 1
    assert q.enqueue(Job(user_id=2, chat_id=2, file_ids=["f2"])) == 2


async def test_enqueue_raises_when_full():
    q = JobQueue(worker_count=1, max_queue_size=2)
    q.enqueue(Job(user_id=1, chat_id=1, file_ids=[]))
    q.enqueue(Job(user_id=2, chat_id=2, file_ids=[]))
    with pytest.raises(QueueFullError):
        q.enqueue(Job(user_id=3, chat_id=3, file_ids=[]))


async def test_worker_processes_jobs_in_order():
    q = JobQueue(worker_count=1)
    processed = []

    async def process(job):
        processed.append(job.user_id)

    q.start(process)
    q.enqueue(Job(user_id=1, chat_id=1, file_ids=["f1"]))
    q.enqueue(Job(user_id=2, chat_id=2, file_ids=["f2"]))
    await q.join()
    assert processed == [1, 2]
    await q.stop()


async def test_worker_count_caps_parallelism():
    q = JobQueue(worker_count=2)
    active = 0
    max_active = 0

    async def process(job):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1

    q.start(process)
    for i in range(4):
        q.enqueue(Job(user_id=i, chat_id=i, file_ids=[]))
    await q.join()
    assert max_active == 2
    await q.stop()


async def test_worker_survives_job_exception():
    q = JobQueue(worker_count=1)
    processed = []

    async def process(job):
        if job.user_id == 1:
            raise RuntimeError("boom")
        processed.append(job.user_id)

    q.start(process)
    q.enqueue(Job(user_id=1, chat_id=1, file_ids=[]))
    q.enqueue(Job(user_id=2, chat_id=2, file_ids=[]))
    await q.join()
    assert processed == [2]  # worker kept going after the failure
    await q.stop()


async def test_stop_cancels_workers():
    q = JobQueue(worker_count=1)
    started = asyncio.Event()

    async def process(job):
        started.set()
        await asyncio.sleep(10)

    q.start(process)
    q.enqueue(Job(user_id=1, chat_id=1, file_ids=[]))
    await started.wait()
    await q.stop()  # must return without hanging


async def test_stats_reports_in_flight_and_queued():
    """``in_flight`` counts jobs picked up but not finished; ``queued`` counts
    jobs still waiting. Regression test for the always-zero ``in_flight`` bug.
    """
    q = JobQueue(worker_count=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def process(job):
        started.set()
        await release.wait()

    q.start(process)
    q.enqueue(Job(user_id=1, chat_id=1, file_ids=[]))
    await started.wait()

    s = q.stats()
    assert s["in_flight"] == 1
    assert s["queued"] == 0

    q.enqueue(Job(user_id=2, chat_id=2, file_ids=[]))
    s = q.stats()
    assert s["in_flight"] == 1
    assert s["queued"] == 1

    release.set()
    await q.join()
    await q.stop()

    s = q.stats()
    assert s["in_flight"] == 0
    assert s["queued"] == 0


async def test_stats_est_wait_uses_batch_time_and_whole_seconds():
    from app.utils import metrics

    metrics.reset_metrics()
    # One batch took 10s; one receipt took 1s. The queue wait must use the
    # batch time (the unit of queueing), not the per-receipt time, and must be
    # a whole number (honest granularity, not false precision).
    metrics.observe("batch_processing_seconds", 10.0)
    metrics.observe("receipt_processing_seconds", 1.0)

    q = JobQueue(worker_count=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def process(job):
        started.set()
        await release.wait()

    q.start(process)
    q.enqueue(Job(user_id=1, chat_id=1, file_ids=[]))
    await started.wait()

    s = q.stats()
    assert s["est_wait_seconds"] == 10  # batch time, whole seconds (not 1, not 10.0)
    release.set()
    await q.join()
    await q.stop()
