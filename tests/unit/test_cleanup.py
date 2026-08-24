"""Tests for guaranteed cleanup."""

import pytest

from app.services.cleanup_service import cleanup_request_dir, sweep_orphaned_requests
from app.services.receipt_service import (
    ProcessingError,
    make_request_base,
    run_with_cleanup,
)


def test_cleanup_removes_tree(tmp_path):
    base = make_request_base(tmp_path, "abc")
    (base / "input" / "a.img").write_bytes(b"x")
    (base / "output" / "a.pdf").write_bytes(b"y")
    assert base.exists()
    cleanup_request_dir(base)
    assert not base.exists()


def test_cleanup_missing_is_noop(tmp_path):
    cleanup_request_dir(tmp_path / "request_missing")  # should not raise


def test_sweep_removes_orphaned_request_dirs(tmp_path):
    # Leftovers from a crashed process (SIGKILL/OOM) must be swept at startup.
    base = make_request_base(tmp_path, "dead1")
    (base / "input" / "a.img").write_bytes(b"x")
    make_request_base(tmp_path, "dead2")
    removed = sweep_orphaned_requests(tmp_path, age_seconds=0)
    assert removed == 2
    assert list(tmp_path.iterdir()) == []


def test_sweep_ignores_non_request_entries(tmp_path):
    other = tmp_path / "keep_me.txt"
    other.write_text("data")
    make_request_base(tmp_path, "dead")
    removed = sweep_orphaned_requests(tmp_path, age_seconds=0)
    assert removed == 1
    assert other.exists()


def test_sweep_keeps_fresh_request_dirs(tmp_path):
    """A request dir younger than the age threshold (possibly a live in-flight
    batch from another instance) must NOT be swept."""
    make_request_base(tmp_path, "fresh")
    removed = sweep_orphaned_requests(tmp_path, age_seconds=600)
    assert removed == 0
    assert (tmp_path / "request_fresh").exists()


def test_sweep_removes_old_request_dirs(tmp_path):
    """A request dir older than the age threshold is a crash orphan and is swept."""
    import os
    import time

    base = make_request_base(tmp_path, "old")
    old = time.time() - 3600  # 1 hour old
    os.utime(base, (old, old))
    removed = sweep_orphaned_requests(tmp_path, age_seconds=600)
    assert removed == 1
    assert not base.exists()


def test_sweep_empty_root_returns_zero(tmp_path):
    assert sweep_orphaned_requests(tmp_path) == 0


def test_cleanup_removes_even_with_files_present(tmp_path):
    base = make_request_base(tmp_path, "z")
    (base / "normalized" / "n.jpg").write_bytes(b"img")
    cleanup_request_dir(base)
    assert not (tmp_path / "request_z").exists()


class _FakeService:
    def __init__(self, behavior="ok"):
        self.behavior = behavior
        self.called = False
        self.base = None

    async def process(self, user_id, file_ids, request_base=None, on_progress=None, *, title=""):
        self.called = True
        self.base = request_base
        if self.behavior == "error":
            raise ProcessingError("boom")
        return request_base


async def test_run_with_cleanup_cleans_on_success(tmp_path):
    svc = _FakeService()
    base = await run_with_cleanup(svc, 1, ["f"], tmp_path)  # type: ignore[arg-type]
    assert svc.called
    assert not base.exists()  # cleaned after success


async def test_run_with_cleanup_cleans_on_failure(tmp_path):
    svc = _FakeService(behavior="error")
    with pytest.raises(ProcessingError):
        await run_with_cleanup(svc, 1, ["f"], tmp_path)  # type: ignore[arg-type]
    assert svc.base is not None and not svc.base.exists()
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("request_")]
    assert leftovers == []
