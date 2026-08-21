"""Image normalization pipeline (Pillow)."""

from __future__ import annotations

import logging
from pathlib import Path

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
        im: Image.Image = Image.open(src)
        with im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            if max(im.size) > max_edge:
                scale = max_edge / float(max(im.size))
                im = im.resize(
                    (int(im.width * scale), int(im.height * scale)),
                    Image.Resampling.LANCZOS,
                )
            im.save(dst, format=OUTPUT_FORMAT, quality=OUTPUT_QUALITY)
    except Exception as exc:  # PIL raises many exception types for corrupt input
        raise ImageNormalizationError(f"Failed to normalize image: {exc}") from exc
    return dst
def get_dimensions(path: str | Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size
