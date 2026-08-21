"""Image normalization pipeline (Pillow)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Cap the longest edge so we don't send absurdly large images to the vision AI.
MAX_EDGE = 1600
OUTPUT_FORMAT = "JPEG"
OUTPUT_QUALITY = 88


class ImageNormalizationError(Exception):
    """Raised when an image cannot be normalized."""


def normalize_image(
    src: str | Path,
    dst: str | Path,
    *,
    max_edge: int = MAX_EDGE,
) -> Path:
    """Normalize ``src`` into ``dst``.

    Steps: decode -> correct EXIF orientation -> strip alpha/colour modes ->
    downscale if needed -> re-encode to JPEG for consistent AI input.
    """
    src = Path(src)
    dst = Path(dst)
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            if max(im.size) > max_edge:
                scale = max_edge / float(max(im.size))
                im = im.resize(
                    (int(im.width * scale), int(im.height * scale)),
                    Image.LANCZOS,
                )
            im.save(dst, format=OUTPUT_FORMAT, quality=OUTPUT_QUALITY)
    except Exception as exc:  # PIL raises many exception types for corrupt input
        raise ImageNormalizationError(f"Failed to normalize image: {exc}") from exc
    return dst


def get_dimensions(path: str | Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def normalize_batch(
    src_dir: str | Path,
    dst_dir: str | Path,
    filenames: list[str],
) -> list[str]:
    """Normalize every named file from src_dir into dst_dir.

    Returns the list of output filenames that normalized successfully.
    """
    src_dir = Path(src_dir)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    done: list[str] = []
    for name in filenames:
        src = src_dir / name
        dst = dst_dir / Path(name).with_suffix(".jpg").name
        try:
            normalize_image(src, dst)
            done.append(dst.name)
        except ImageNormalizationError:
            logger.warning("normalization failed for %s", name)
    return done
