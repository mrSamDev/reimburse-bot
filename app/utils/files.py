"""File helpers: format sniffing, safe naming, size validation."""

from __future__ import annotations

from pathlib import Path

# Telegram media type -> file extension
MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def sniff_format(path: str | Path) -> str | None:
    """Detect image format by magic bytes using Pillow."""
    from PIL import Image

    try:
        with Image.open(path) as img:
            fmt = (img.format or "").upper()
    except Exception:
        return None
    mapping = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    return mapping.get(fmt)


def ext_for_mime(mime: str) -> str:
    return MIME_TO_EXT.get(mime, "")


def is_supported_mime(mime: str | None) -> bool:
    return (mime or "") in MIME_TO_EXT


def human_size(bytes_: int) -> str:
    return f"{bytes_ / (1024 * 1024):.2f} MB"
