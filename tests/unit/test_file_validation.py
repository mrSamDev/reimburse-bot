"""Tests for file validation, image normalization and temp storage."""

import pytest

from app.services.file_validation import (
    CorruptImageError,
    OversizedFileError,
    UnsupportedTypeError,
    validate_downloaded_image,
)
from app.utils.images import ImageNormalizationError, get_dimensions, normalize_image
from tests.conftest import (
    make_corrupt,
    make_image,
    make_large_image,
    make_oversized,
    make_rotated_image,
)


class TestFileValidation:
    def test_valid_jpeg(self, tmp_path):
        p = make_image(tmp_path / "valid.jpg", "JPEG")
        v = validate_downloaded_image(p, max_size_mb=10)
        assert v.mime == "image/jpeg"
        assert v.width == 200 and v.height == 120

    def test_valid_png(self, tmp_path):
        p = make_image(tmp_path / "valid.png", "PNG")
        assert validate_downloaded_image(p).mime == "image/png"

    def test_valid_webp(self, tmp_path):
        p = make_image(tmp_path / "valid.webp", "WEBP")
        assert validate_downloaded_image(p).mime == "image/webp"

    def test_corrupt_rejected(self, tmp_path):
        p = make_corrupt(tmp_path / "corrupt.jpg")
        with pytest.raises(CorruptImageError):
            validate_downloaded_image(p)

    def test_oversized_rejected(self, tmp_path):
        p = make_oversized(tmp_path / "oversized.jpg", 11 * 1024 * 1024)
        with pytest.raises(OversizedFileError):
            validate_downloaded_image(p, max_size_mb=10)

    def test_unsupported_mime_rejected(self, tmp_path):
        p2 = tmp_path / "fake.pdf"
        p2.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises((CorruptImageError, UnsupportedTypeError)):
            validate_downloaded_image(p2)


class TestNormalization:
    def test_normalize_jpeg(self, tmp_path):
        src = make_image(tmp_path / "a.jpg", "JPEG")
        dst = normalize_image(src, tmp_path / "out.jpg")
        assert dst.exists()
        assert get_dimensions(dst) == (200, 120)

    def test_normalize_png(self, tmp_path):
        src = make_image(tmp_path / "a.png", "PNG")
        out = normalize_image(src, tmp_path / "out.jpg")
        assert out.suffix == ".jpg"

    def test_normalize_webp(self, tmp_path):
        src = make_image(tmp_path / "a.webp", "WEBP")
        out = normalize_image(src, tmp_path / "out.jpg")
        assert out.exists()

    def test_rotated_image_orientation_corrected(self, tmp_path):
        src = make_rotated_image(tmp_path / "rot.png")
        out = normalize_image(src, tmp_path / "out.jpg")
        # EXIF 6 transposes 300x100 -> 100x300
        w, h = get_dimensions(out)
        assert (w, h) == (100, 300)

    def test_large_image_downscaled(self, tmp_path):
        src = make_large_image(tmp_path / "big.jpg", pixel=3000)
        out = normalize_image(src, tmp_path / "out.jpg")
        w, h = get_dimensions(out)
        assert max(w, h) <= 1600

    def test_corrupt_raises(self, tmp_path):
        src = make_corrupt(tmp_path / "bad.jpg")
        with pytest.raises(ImageNormalizationError):
            normalize_image(src, tmp_path / "out.jpg")
