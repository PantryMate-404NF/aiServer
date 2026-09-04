"""OCR 입력 정규화. 화소 상한이 응답 시간을, 대비 보정이 인식률을 좌우합니다."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

import config
from features.receipt.pipeline import s1_preprocess as preprocess
from features.receipt.pipeline.s1_preprocess import to_ocr_input
from utils.errors import ImageDecodeError

MAX_PIXELS = 1_500_000
EXIF_ORIENTATION_TAG = 274
EXIF_ROTATE_90 = 6


def _png_bytes(width: int, height: int) -> bytes:
    rng = np.random.default_rng(seed=0)
    image = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return bytes(buffer.tobytes())


def test_oversized_image_is_scaled_under_the_pixel_cap() -> None:
    """상한이 빠지면 4.9MP 영수증 한 장이 100초를 넘겨 동기 API 가 깨집니다."""
    result = to_ocr_input(_png_bytes(2000, 3000))

    height, width = result.shape[:2]
    assert height * width <= MAX_PIXELS
    assert width / height == pytest.approx(2000 / 3000, rel=0.01)


def test_small_image_keeps_its_size() -> None:
    """이미 작은 이미지는 건드리지 않습니다. 긴 변 기준이었다면 폭이 뭉개졌을 크기입니다."""
    result = to_ocr_input(_png_bytes(362, 2324))

    assert result.shape[:2] == (2324, 362)


def test_output_is_three_channel_uint8() -> None:
    """PaddleOCR 이 받는 형태입니다. 채널 수가 바뀌면 워커에서 터집니다."""
    result = to_ocr_input(_png_bytes(100, 100))

    assert result.shape == (100, 100, 3)
    assert result.dtype == np.uint8


def test_pixel_cap_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_MAX_PIXELS", "10000")
    config.get_settings.cache_clear()

    result = to_ocr_input(_png_bytes(400, 400))

    assert result.shape[0] * result.shape[1] <= 10000


def test_undecodable_bytes_raise_a_specific_error() -> None:
    """조용히 빈 결과를 돌려주면 어디서 실패했는지 알 수 없습니다."""
    with pytest.raises(ImageDecodeError):
        to_ocr_input(b"not an image")


def _jpeg_with_orientation(width: int, height: int, orientation: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (width, height), "white")
    exif = image.getexif()
    exif[EXIF_ORIENTATION_TAG] = orientation
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _heic_bytes(width: int, height: int) -> bytes:
    """아이폰 기본 촬영 포맷. OpenCV 가 못 여는 대표적인 입력입니다."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="HEIF")
    return buffer.getvalue()


def test_heic_goes_through_the_pillow_fallback() -> None:
    """OpenCV 는 HEIC 를 열지 못합니다. 이 경로가 없으면 아이폰 사진이 전부 실패합니다."""
    assert cv2.imdecode(np.frombuffer(_heic_bytes(400, 100), np.uint8), cv2.IMREAD_COLOR) is None

    result = to_ocr_input(_heic_bytes(400, 100))

    assert result.shape[:2] == (100, 400)


def test_pillow_fallback_applies_exif_orientation(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenCV 는 EXIF 회전을 알아서 적용합니다. 대체 경로도 같아야 합니다.

    이게 빠지면 세로로 찍은 사진이 옆으로 누운 채 OCR 로 넘어가고, 방향 보정 모듈을
    전부 꺼 둔 탓에 인식이 통째로 실패합니다.
    """
    monkeypatch.setattr(preprocess.cv2, "imdecode", lambda buffer, flags: None)

    result = to_ocr_input(_jpeg_with_orientation(400, 100, EXIF_ROTATE_90))

    height, width = result.shape[:2]
    assert (width, height) == (100, 400)


def test_opencv_path_applies_exif_orientation() -> None:
    """두 경로가 같은 결과를 내는지 확인합니다. 어긋나면 포맷에 따라 결과가 갈립니다."""
    result = to_ocr_input(_jpeg_with_orientation(400, 100, EXIF_ROTATE_90))

    height, width = result.shape[:2]
    assert (width, height) == (100, 400)
