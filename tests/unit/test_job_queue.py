"""Tests for the in-process JobQueue drained by background workers."""

import asyncio

from app.bot.queue import Job, JobQueue


async def test_enqueue_returns_position():
    q = JobQueue(worker_count=1)
    assert q.enqueue(Job(user_id=1, chat_id=1, file_ids=["f1"])) == 1
    assert q.enqueue(Job(user_id=2, chat_id=2, file_ids=["f2"])) == 2


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
