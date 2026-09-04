"""OCR 에 넣기 전 이미지 정규화. 크기 상한과 국소 대비 보정 두 가지입니다."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image as PillowImage
from PIL import ImageOps
from pillow_heif import register_heif_opener

from config import get_settings
from utils.errors import ImageDecodeError

logger = logging.getLogger(__name__)

Image = NDArray[np.uint8]

# 아이폰 기본 촬영 포맷인 HEIC 를 PIL 이 열 수 있게 합니다. 프로세스당 한 번이면 됩니다.
register_heif_opener()


def to_ocr_input(data: bytes) -> Image:
    """업로드 바이트를 OCR 이 읽을 이미지로 바꿉니다.

    총 화소 상한이 빠지면 응답 시간이 무너집니다. 4.9MP 원본이 136.8초, 1.2MP 로 줄이면
    7.9초인데 검출된 줄 수는 24와 23으로 사실상 같았습니다. 긴 변이 아니라 총 화소로
    재는 이유는 영수증이 세로로 길고 좁아 긴 변 기준이 전자영수증의 폭을 뭉개기 때문입니다.

    대비 보정은 전역이 아니라 국소로 합니다. 영수증 사진의 문제는 한쪽만 밝은 조명,
    접힌 자국의 그림자, 감열지의 부분 변색이라 전역 히스토그램으로는 펴지지 않습니다.
    """
    settings = get_settings()

    image = _decode(data)

    height, width = image.shape[:2]
    pixels = height * width
    if pixels > settings.ocr_max_pixels:
        scale = (settings.ocr_max_pixels / pixels) ** 0.5
        image = cast(
            Image,
            cv2.resize(
                image,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            ),
        )
        logger.info(
            "ocr input resized %dx%d -> %dx%d", width, height, image.shape[1], image.shape[0]
        )

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(
        clipLimit=settings.ocr_clahe_clip_limit,
        tileGridSize=(settings.ocr_clahe_tile_grid, settings.ocr_clahe_tile_grid),
    )
    # OpenCV 스텁이 dtype 을 좁혀 주지 않습니다. 입력이 uint8 이면 출력도 uint8 입니다.
    return cast(Image, cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR))


def _decode(data: bytes) -> Image:
    """바이트를 BGR 이미지로 엽니다. OpenCV 가 모르는 포맷은 PIL 로 한 번 더 시도합니다."""
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is not None:
        return cast(Image, decoded)

    try:
        with PillowImage.open(BytesIO(data)) as opened:
            # OpenCV 는 EXIF 회전을 알아서 적용합니다. PIL 은 하지 않으므로 여기서 맞춥니다.
            # 아이폰이 세로로 찍은 HEIC 가 옆으로 누운 채 OCR 로 넘어가면, 방향 보정
            # 모듈을 전부 꺼 둔 탓에 사실상 인식 실패가 됩니다.
            rgb = np.array(ImageOps.exif_transpose(opened).convert("RGB"))
    except (OSError, ValueError) as error:
        raise ImageDecodeError(f"이미지를 디코딩하지 못했습니다 (bytes={len(data)})") from error
    return cast(Image, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
