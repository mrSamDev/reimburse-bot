"""In-process FIFO job queue drained by background workers.

Decouples the ``/generate`` handler from the blocking receipt-processing work:
the handler enqueues a :class:`Job` and returns immediately, and a bounded pool
of workers performs the actual download/AI/PDF work asynchronously. The worker
count is the global concurrency cap (how many batches run at once), which also
bounds concurrent AI calls and transient tmpfs disk usage.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    """Raised when the job queue is at capacity and a job cannot be enqueued."""


@dataclass
class Job:
    """A single receipt-processing request captured at enqueue time."""

    user_id: int
    chat_id: int
    file_ids: list[str] = field(default_factory=list)
    title: str = ""


class JobQueue:
    """FIFO queue of :class:`Job` objects drained by ``worker_count`` workers.

    ``enqueue`` returns the job's 1-based position in the queue (not counting
    jobs already being processed) so the bot can tell the user how long they
    wait. ``join`` lets tests (and shutdown) wait for in-flight work to finish.
    """

    def __init__(self, worker_count: int = 2, max_queue_size: int = 50) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_count = worker_count
        self._workers: list[asyncio.Task] = []
        self._enqueued = 0
        self._started = 0

    def enqueue(self, job: Job) -> int:
        """Add ``job`` to the queue; return its 1-based position in line.

        Raises :class:`QueueFullError` when the queue is at capacity, so the
        caller can reject the user gracefully instead of buffering unboundedly.
        """
        self._enqueued += 1
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self._enqueued -= 1  # roll back so the position counter stays accurate
            raise QueueFullError("job queue is full") from None
        return self._enqueued - self._started

    def start(self, process_job: Callable[[Job], Awaitable[None]]) -> None:
        """Spawn ``worker_count`` worker tasks that drain the queue.

        Idempotent: calling twice does not spawn a second pool. ``process_job``
        is an async callable taking a :class:`Job`; a raised exception is logged
        and the worker continues with the next job.
        """
        if self._workers:
            return
        loop = asyncio.get_running_loop()
        for _ in range(self._worker_count):
            self._workers.append(loop.create_task(self._worker(process_job)))

    async def stop(self) -> None:
        """Cancel and await all workers (used at shutdown)."""
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers.clear()

    async def join(self) -> None:
        """Wait until every currently-queued job has been processed."""
        await self._queue.join()

    async def _worker(self, process_job: Callable[[Job], Awaitable[None]]) -> None:
        while True:
            job = await self._queue.get()
            self._started += 1
            try:
                await process_job(job)
            except Exception:
                logger.exception("job failed for user %s", job.user_id)
            finally:
                self._queue.task_done()
