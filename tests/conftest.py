"""Shared fixture builders used across tests."""

from pathlib import Path

from PIL import Image


def make_image(path: Path, fmt: str, size=(200, 120), color=(10, 20, 30)) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    img.save(path, format=fmt)
    return path


def make_rotated_image(path: Path) -> Path:
    """Create a PNG with EXIF orientation 6 (needs transposition)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (300, 100), (200, 30, 30))
    # PIL exposes EXIF orientation via the exif kwarg.
    exif = Image.Exif()
    exif[274] = 6
    img.save(path, format="PNG", exif=exif)
    return path


def make_corrupt(path: Path, valid_bytes: bytes = b"\xff\xd8\xff\xe0") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(valid_bytes + b"\x00" * 64)  # JPEG magic but truncated/garbage
    return path


def make_oversized(path: Path, size: int) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)
    return path


def make_large_image(path: Path, pixel=6000) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (pixel, pixel), (5, 5, 5))
    img.save(path, format="JPEG")
    return path
