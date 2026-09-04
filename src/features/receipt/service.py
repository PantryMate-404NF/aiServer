"""영수증 1장을 단계 순서대로 조립합니다. 구현 상세는 각 단계 파일에 있습니다."""

from __future__ import annotations

import logging
from time import perf_counter

from features.receipt.pipeline import s1_preprocess, s2_ocr, s3_parse, s5_normalize
from features.receipt.schema import OcrCell, ReceiptItem, ReceiptResponse
from utils.errors import (
    ExternalServiceError,
    ImageDecodeError,
    LlmUnavailableError,
    OcrEmptyError,
)

logger = logging.getLogger(__name__)

MILLISECONDS_PER_SECOND = 1000


async def parse_receipt_image(receipt_id: str, data: bytes) -> ReceiptResponse:
    """이미지 한 장에서 구매일과 식재료 품목을 뽑습니다.

    마스킹은 여기서 부르지 않습니다. 프롬프트를 만드는 함수가 유일한 통로이고,
    이 함수가 직접 부르면 그 통로를 우회하는 두 번째 길이 생깁니다.
    """
    ocr_started = perf_counter()
    # 자리를 먼저 잡고 디코딩합니다. 밖에서 디코딩하면 대기 요청이 이미지 배열을 들고
    # 줄을 서게 되어 동시에 몰린 만큼 메모리가 늘어납니다.
    async with s2_ocr.slot():
        try:
            image = s1_preprocess.to_ocr_input(data)
        except ImageDecodeError as error:
            raise OcrEmptyError(receipt_id) from error
        cells = await s2_ocr.read(image)
    ocr_ms = (perf_counter() - ocr_started) * MILLISECONDS_PER_SECOND

    text = s3_parse.group_lines(cells)
    if not text.strip():
        logger.warning("ocr found no text receipt_id=%s ocr_ms=%.0f", receipt_id, ocr_ms)
        raise OcrEmptyError(receipt_id)

    llm_started = perf_counter()
    try:
        parsed = await s5_normalize.parse_receipt(text)
    except ExternalServiceError as error:
        raise LlmUnavailableError(receipt_id) from error
    llm_ms = (perf_counter() - llm_started) * MILLISECONDS_PER_SECOND

    foods = [item for item in parsed.items if item.is_food]
    logger.info(
        "receipt parsed receipt_id=%s ocr_ms=%.0f llm_ms=%.0f cells=%d items=%d dropped=%d "
        "confidence=%.3f",
        receipt_id,
        ocr_ms,
        llm_ms,
        len(cells),
        len(foods),
        len(parsed.items) - len(foods),
        _mean_confidence(cells),
    )

    return ReceiptResponse(
        receipt_id=receipt_id,
        purchased_at=parsed.purchased_at,
        # 사전이 없어 ingredient_id 는 항상 null 입니다. 미매칭 항목을 버리지 않습니다.
        items=[ReceiptItem(name=item.name) for item in foods],
    )


def _mean_confidence(cells: list[OcrCell]) -> float:
    """영수증 단위 인식 신뢰도. 응답에 싣지 않고 로그로만 남깁니다."""
    if not cells:
        return 0.0
    return sum(cell.score for cell in cells) / len(cells)
