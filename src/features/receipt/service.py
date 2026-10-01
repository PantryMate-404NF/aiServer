"""영수증 1장을 단계 순서대로 조립합니다. 구현 상세는 각 단계 파일에 있습니다."""

from __future__ import annotations

import logging
from time import perf_counter

from features.receipt import monitor
from features.receipt.pipeline import s1_preprocess, s2_ocr, s3_parse, s5_normalize
from features.receipt.schema import OcrCell, ReceiptItem, ReceiptResponse
from utils.errors import (
    ExternalServiceError,
    ImageDecodeError,
    LlmQuotaExceededError,
    LlmUnavailableError,
    OcrBusyError,
    OcrEmptyError,
    OcrPoolNotReadyError,
    OcrUnavailableError,
    QuotaExceededError,
    ReceiptError,
)

logger = logging.getLogger(__name__)


async def parse_receipt_image(receipt_id: str, data: bytes) -> ReceiptResponse:
    """이미지 한 장에서 구매일과 식재료 품목을 뽑습니다.

    실패는 전부 ReceiptError 로 바꿔서 올립니다. 그 밖의 예외가 새면 계약된 500 본문
    없이 나갑니다. 지표는 여기서 한 번만 셉니다.
    """
    started = perf_counter()
    try:
        return await _parse(receipt_id, data, started)
    except ReceiptError as error:
        monitor.observe_failure(error.code, perf_counter() - started)
        raise


async def _parse(receipt_id: str, data: bytes, started: float) -> ReceiptResponse:
    """단계 조립. 마스킹은 여기서 부르지 않습니다.

    프롬프트를 만드는 함수가 마스킹의 유일한 통로이고, 이 함수가 직접 부르면 그 통로를
    우회하는 두 번째 길이 생깁니다.
    """
    try:
        # 자리를 먼저 잡고 디코딩합니다. 밖에서 디코딩하면 대기 요청이 이미지 배열을 들고
        # 줄을 서게 되어 동시에 몰린 만큼 메모리가 늘어납니다.
        async with s2_ocr.slot():
            ocr_started = perf_counter()
            try:
                image = s1_preprocess.to_ocr_input(data)
            except ImageDecodeError as error:
                raise OcrEmptyError(receipt_id) from error
            cells = await s2_ocr.read(image)
    except (OcrPoolNotReadyError, OcrBusyError) as error:
        logger.warning("ocr unavailable receipt_id=%s: %s", receipt_id, error)
        raise OcrUnavailableError(receipt_id) from error
    ocr_s = perf_counter() - ocr_started

    text = s3_parse.group_lines(cells)
    if not text.strip():
        # 검출 조각 수를 남깁니다. 런북은 인식 품질 하락 때 이 수가 줄었는지부터 봅니다.
        logger.warning(
            "ocr found no text receipt_id=%s ocr_s=%.2f cells=%d", receipt_id, ocr_s, len(cells)
        )
        raise OcrEmptyError(receipt_id)

    llm_started = perf_counter()
    try:
        parsed = await s5_normalize.parse_receipt(text)
    except QuotaExceededError as error:
        logger.warning("llm quota exceeded receipt_id=%s: %s", receipt_id, error)
        raise LlmQuotaExceededError(receipt_id) from error
    except ExternalServiceError as error:
        # 응답 코드는 하나라, 원인(시간 초과인지 형식 오류인지)은 이 로그로 가립니다.
        logger.warning("llm failed receipt_id=%s: %s", receipt_id, error)
        raise LlmUnavailableError(receipt_id) from error
    llm_s = perf_counter() - llm_started

    # 이름이 비거나 글자가 없는 항목은 스키마 검증이 이미 뺐습니다. 여기서는 식재료만 고릅니다.
    foods = [item for item in parsed.items if item.is_food]
    confidence = _mean_confidence(cells)
    logger.info(
        "receipt parsed receipt_id=%s ocr_s=%.2f llm_s=%.2f cells=%d items=%d dropped=%d "
        "confidence=%.3f",
        receipt_id,
        ocr_s,
        llm_s,
        len(cells),
        len(foods),
        len(parsed.items) - len(foods),
        confidence,
    )
    monitor.observe_success(
        ocr_s=ocr_s,
        llm_s=llm_s,
        total_s=perf_counter() - started,
        confidence=confidence,
        cells=len(cells),
        items=len(foods),
        dropped=len(parsed.items) - len(foods),
    )

    return ReceiptResponse(
        receipt_id=receipt_id,
        purchased_at=parsed.purchased_at,
        # 사전이 없어 ingredient_id 는 항상 null 입니다. 미매칭 항목을 버리지 않습니다.
        items=[ReceiptItem(name=item.name) for item in foods],
        confidence=round(confidence, 3),
    )


def _mean_confidence(cells: list[OcrCell]) -> float:
    """영수증 단위 인식 신뢰도. 조각별 인식 점수의 단순 평균입니다."""
    if not cells:
        return 0.0
    return sum(cell.score for cell in cells) / len(cells)
