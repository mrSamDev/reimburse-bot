"""Request-scoped temporary directory management."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path


class TempService:
    """Creates and destroys an isolated request directory per job.

    Layout:
        temp/request_<uuid>/
            input/
            normalized/
            output/
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create_request_dir(self, request_id: str | None = None) -> Path:
        rid = request_id or uuid.uuid4().hex[:6]
        base = self.root / f"request_{rid}"
        (base / "input").mkdir(parents=True, exist_ok=True)
        (base / "normalized").mkdir(parents=True, exist_ok=True)
        (base / "output").mkdir(parents=True, exist_ok=True)
        return base

    def input_dir(self, base: str | Path) -> Path:
        return Path(base) / "input"

    def normalized_dir(self, base: str | Path) -> Path:
        return Path(base) / "normalized"

    def output_dir(self, base: str | Path) -> Path:
        return Path(base) / "output"

    def cleanup(self, base: str | Path) -> None:
        """Remove the whole request directory tree if it exists."""
        p = Path(base)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
