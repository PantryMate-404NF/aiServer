"""영수증 도메인의 경계를 넘는 데이터 구조."""

from __future__ import annotations

import logging
from datetime import date
from typing import NamedTuple

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# 팬트리 등록 폼(PANTRY-003)의 품목명 입력 상한입니다.
MAX_ITEM_NAME_LENGTH = 20


class OcrCell(NamedTuple):
    """OCR 이 찾은 텍스트 조각 하나.

    PaddleOCR 의 원시 반환값을 이 형태로 바꿔서 내보냅니다. 원시 형식은 버전마다
    바뀌므로 파이프라인 뒷단이 그것에 의존하면 안 됩니다.

    좌표는 전처리를 마친 이미지 기준 픽셀입니다. 원본 좌표가 아닙니다.
    프로세스 풀 경계를 넘으므로 pickle 이 되는 형태로 둡니다.
    """

    text: str
    x_left: float
    y_center: float
    height: float
    score: float


class ParsedItem(BaseModel):
    """LLM 이 뽑아낸 품목 하나. 신뢰하지 않고 여기서 한 번 다듬습니다."""

    name: str
    # 응답에는 싣지 않는 내부 필터입니다. 비식재료를 걸러내는 데만 씁니다.
    is_food: bool

    @field_validator("name")
    @classmethod
    def _fit_to_the_form(cls, value: str) -> str:
        """폼 상한을 넘는 이름은 자릅니다. 길다고 품목을 버리지는 않습니다.

        프롬프트가 이미 20자를 요청하지만 LLM 이 지킨다는 보장이 없고, 넘긴 값이 그대로
        나가면 등록 화면에서 잘립니다. 들어오는 경계에서 한 번 맞춰 둡니다.
        """
        name = value.strip()
        if len(name) <= MAX_ITEM_NAME_LENGTH:
            return name
        logger.info("item name over %d chars, truncated", MAX_ITEM_NAME_LENGTH)
        return name[:MAX_ITEM_NAME_LENGTH]


class ParsedReceipt(BaseModel):
    """LLM 응답을 검증한 결과. 검증을 통과한 것만 파이프라인 뒷단으로 넘어갑니다."""

    purchased_at: date | None = None
    items: list[ParsedItem] = []

    @field_validator("purchased_at", mode="before")
    @classmethod
    def _drop_unreadable_date(cls, value: object) -> object:
        """날짜를 못 읽었다고 영수증 전체를 실패시키지 않습니다. 등록을 막지 않는 값입니다."""
        if value is None or value == "":
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            logger.warning("purchased_at is not an ISO date, dropping it: %r", value)
            return None


class ReceiptItem(BaseModel):
    """응답에 나가는 품목. is_food 는 내부 필터라 여기에 없습니다."""

    name: str
    # 사전이 구축되기 전까지 항상 null 입니다. 미매칭 항목을 버리지 않습니다.
    ingredient_id: int | None = None


class ReceiptResponse(BaseModel):
    """성공 응답. 처리 시간과 모델 버전은 싣지 않고 로그로만 남깁니다."""

    receipt_id: str
    purchased_at: date | None = None
    items: list[ReceiptItem]


class ReceiptErrorDetail(BaseModel):
    """실패 사유. code 는 계약된 두 값 중 하나이고 message 는 사용자에게 보여도 됩니다."""

    code: str
    message: str


class ReceiptErrorResponse(BaseModel):
    """실패 응답. 성공 응답과 같은 구조에 error 만 더합니다.

    호출부가 성공과 실패에서 다른 모양을 다루지 않도록 `purchased_at` 과 `items` 를
    빈 값으로 함께 싣습니다. 성공 응답에는 반대로 `error` 가 없습니다.
    """

    receipt_id: str
    purchased_at: date | None = None
    items: list[ReceiptItem] = []
    error: ReceiptErrorDetail
