"""Isolated image-processing regression tests: no database or external API needed."""
from io import BytesIO
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from property_media import prepare_image


def png(width, height, *, metadata=None):
    image = Image.new("RGB", (width, height), (120, 90, 60))
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def test_cover_reencoded_as_jpeg_without_uploaded_metadata():
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", "private-owner-information")
    result = prepare_image(png(800, 600, metadata=meta), "cover")
    assert result.startswith(b"\xff\xd8")
    assert b"private-owner-information" not in result
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (800, 600)


def test_panorama_accepts_valid_two_to_one_image():
    result = prepare_image(png(1024, 512), "panorama")
    with Image.open(BytesIO(result)) as image:
        assert image.size == (1024, 512)


@pytest.mark.parametrize("width,height", [(800, 400), (1024, 600), (1200, 1200)])
def test_panorama_rejects_small_or_wrong_aspect_ratio(width, height):
    with pytest.raises(HTTPException) as error:
        prepare_image(png(width, height), "panorama")
    assert error.value.status_code == 422


def test_invalid_image_rejected():
    with pytest.raises(HTTPException) as error:
        prepare_image(b"not an image", "cover")
    assert error.value.status_code == 422
