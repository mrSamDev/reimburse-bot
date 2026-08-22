"""Guaranteed-cleanup wrapper around the processing pipeline."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.services.cleanup_service import cleanup_request_dir

from .pipeline import ProcessingService
from .types import ProcessingResult, make_request_base


async def run_with_cleanup(
    service: ProcessingService,
    user_id: int,
    file_ids: list[str],
    temp_root: str | Path,
    deliver=None,
    request_id: str | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    *,
    title: str = "",
) -> ProcessingResult:
    """Run processing, deliver the report, then guarantee cleanup.

    ``deliver`` runs while the PDF still exists; ``request_id`` correlates
    logs; ``on_progress`` is an optional ``(done, total)`` per-receipt callback.
    """
    request_id = request_id or uuid.uuid4().hex[:6]
    base = make_request_base(temp_root, request_id)
    try:
        result = await service.process(
            user_id, file_ids, request_base=base, on_progress=on_progress, title=title
        )
        if deliver is not None:
            await deliver(result)
        return result
    finally:
        cleanup_request_dir(base)
