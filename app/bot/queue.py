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

from app.utils import metrics

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
        self._completed = 0

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

    def stats(self) -> dict:
        """Snapshot of queue state for observability (health/metrics endpoint).

        ``queued`` is the number of jobs waiting to be picked up; ``in_flight``
        is jobs currently being processed by a worker. ``position`` is the
        1-based line position a newly enqueued job would get. ``est_wait_seconds``
        is a rough whole-second estimate of how long a new job would wait before
        a worker picks it up, assuming each in-flight job still has roughly the
        average observed *batch* processing time left and each queued job takes
        that long too.

        All values are derived from monotonic integer counters (``_enqueued`` /
        ``_started`` / ``_completed``) rather than ``asyncio.Queue.qsize()``, so
        this is safe to call from the health-server thread while the event loop
        mutates the queue.
        """
        queued = self._enqueued - self._started
        in_flight = self._started - self._completed
        if queued < 0:
            queued = 0
        if in_flight < 0:
            in_flight = 0
        avg = 0.0
        count = metrics.get_metrics().get("batch_processing_seconds_count", 0)
        total = metrics.get_metrics().get("batch_processing_seconds_sum", 0.0)
        if count:
            avg = total / count
        # Estimated wait: (in_flight + queued) across the pool × avg batch time.
        est_wait = ((in_flight + queued) / self._worker_count) * avg if self._worker_count else 0.0
        return {
            "queued": queued,
            "in_flight": in_flight,
            "max_queue_size": self._queue.maxsize,
            "worker_count": self._worker_count,
            "workers_active": len(self._workers),
            "enqueued_total": self._enqueued,
            "started_total": self._started,
            "completed_total": self._completed,
            "position": self._enqueued - self._started,
            "est_wait_seconds": round(est_wait),
        }

    async def _worker(self, process_job: Callable[[Job], Awaitable[None]]) -> None:
        while True:
            job = await self._queue.get()
            self._started += 1
            try:
                await process_job(job)
            except Exception:
                logger.exception("job failed for user %s", job.user_id)
            finally:
                self._completed += 1
                self._queue.task_done()
