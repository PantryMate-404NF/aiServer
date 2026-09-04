"""영수증 도메인의 입출력 경계. 요청 파싱과 응답 직렬화만 담당합니다."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from config import get_settings
from features.receipt import service
from features.receipt.schema import ReceiptResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ocr", tags=["receipt"])


@router.post("/receipt")
async def parse_receipt(
    receipt_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> ReceiptResponse:
    """영수증 이미지 1장을 받아 구매일과 식재료 품목을 돌려줍니다.

    매수·포맷·용량 검증은 프론트와 백엔드가 앞단에서 합니다. 여기서는 필드가 빠졌을 때만
    400 이고, 처리 실패는 전부 500 입니다.
    """
    _reject_oversized(receipt_id, file)
    return await service.parse_receipt_image(receipt_id, await file.read())


def _reject_oversized(receipt_id: str, file: UploadFile) -> None:
    """계약을 어긴 크기의 요청을 거릅니다. 필드 누락과 같은 400 으로 돌려보냅니다.

    앞단에서 이미 거르기로 되어 있으므로 여기 걸리는 것은 호출자 버그입니다. 사용자에게
    보일 문구를 담지 않고 로그에만 남깁니다. 바이트 자체를 막는 것은 이 앞의 리버스
    프록시가 할 일이고, 여기서는 디코딩과 OCR 로 넘어가는 것만 막습니다.
    """
    settings = get_settings()
    if len(receipt_id) > settings.max_receipt_id_length:
        logger.warning("receipt_id is too long: %d chars", len(receipt_id))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    if file.size is not None and file.size > settings.max_upload_bytes:
        logger.warning("upload is too large receipt_id=%s bytes=%d", receipt_id, file.size)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
