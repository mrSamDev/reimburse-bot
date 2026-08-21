"""Receipt file validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.utils import files as file_utils


class FileValidationError(Exception):
    """Base for file validation failures."""


class UnsupportedTypeError(FileValidationError):
    pass


class OversizedFileError(FileValidationError):
    pass


class CorruptImageError(FileValidationError):
    pass


class ZeroDimensionError(FileValidationError):
    pass


@dataclass
class FileValidation:
    mime: str
    size_bytes: int
    width: int
    height: int


def validate_downloaded_image(
    path: str | Path,
    *,
    max_size_mb: int = 10,
) -> FileValidation:
    """Validate an already-downloaded image file.

    Checks MIME type by magic bytes, size and Pillow-decodability + dimensions.
    """
    p = Path(path)
    size_bytes = p.stat().st_size
    if size_bytes > max_size_mb * 1024 * 1024:
        raise OversizedFileError(
            f"File exceeds {max_size_mb} MB limit ({file_utils.human_size(size_bytes)})"
        )
    mime = file_utils.sniff_format(p)
    if mime is None:
        raise CorruptImageError("File is not a decodable image")
    if not file_utils.is_supported_mime(mime):
        raise UnsupportedTypeError(f"Unsupported image type: {mime}")

    from app.utils import images

    try:
        w, h = images.get_dimensions(p)
    except Exception as exc:
        raise CorruptImageError(f"Image integrity check failed: {exc}") from exc
    if w <= 0 or h <= 0:
        raise ZeroDimensionError("Image has invalid dimensions")
    return FileValidation(mime=mime, size_bytes=size_bytes, width=w, height=h)
