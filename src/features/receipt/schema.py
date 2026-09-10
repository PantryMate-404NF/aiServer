"""영수증 도메인의 경계를 넘는 데이터 구조."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import NamedTuple

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# 팬트리 등록 폼(PANTRY-003)의 품목명 입력 상한입니다.
MAX_ITEM_NAME_LENGTH = 20

# 영수증 줄 맨 앞의 행 번호와 표시 기호입니다. OCR 이 품목명에 붙여서 읽어 옵니다.
# 예: "01델몬트", "02*베로나", "005P하선정", "(G)피코크", "*레쉬센터".
# 0 과 O, 8 과 B 는 감열지에서 서로 바뀌어 읽히므로 함께 받습니다.
# 이 일을 프롬프트에 맡겼더니 교정 금지 지시와 부딪혀 불안정했습니다. 규칙이 분명한
# 잘라내기는 여기서 확정하고, LLM 에는 품목인지와 식재료인지만 맡깁니다.
ROW_NUMBER_PREFIX = re.compile(
    r"^(?:\(\w{1,2}\)|[0-9OB]{1,3}\s*\*|0[0-9OB]{1,2}P?|[0-9OB]{1,3}P|P(?=[가-힣])|\*)\s*"
)
# 줄 묶기가 셀 사이에 넣는 구분자입니다. 전자영수증에서 이름이 두 줄로 꺾이면 LLM 이
# 다음 칸을 이어 붙이면서 구분자를 그대로 두는 경우가 있습니다. 프롬프트가 지우라고
# 해도 지켜지지 않아 여기서 확정합니다. 꺾인 자리는 단어 중간이라 공백 없이 잇습니다.
# 예: "카스텔크림레몬캔디(50 | g)" -> "카스텔크림레몬캔디(50g)"
CELL_SEPARATOR_IN_NAME = re.compile(r"\s*\|\s*")
# 품목명이라면 한글이나 영문이 한 글자는 있어야 합니다. 저해상도 사진에서 LLM 이
# "10", "()" 같은 조각을 품목으로 내보내는데, 등록 폼에 넣을 수 있는 이름이 아닙니다.
HAS_LETTER = re.compile(r"[가-힣A-Za-z]")


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
    # 상자 윗변의 기울기(dy/dx). 글자 줄이 얼마나 누웠는지를 상자 자신이 말해 줍니다.
    # 셀 위치로 회귀하면 레이블 열과 값 열이 나뉜 2열 배치가 기울기로 오인됩니다.
    # 한두 글자짜리 짧은 상자는 검출 오차가 각도를 그대로 흔들어 재지 않고 None 입니다.
    slope: float | None = None


class ParsedItem(BaseModel):
    """LLM 이 뽑아낸 품목 하나. 신뢰하지 않고 여기서 한 번 다듬습니다."""

    name: str
    # 응답에는 싣지 않는 내부 필터입니다. 비식재료를 걸러내는 데만 씁니다.
    is_food: bool

    @field_validator("name")
    @classmethod
    def _clean_up(cls, value: str) -> str:
        """구분자와 행 번호처럼 이름이 아닌 것을 떼고 폼 상한에 맞춥니다. 글자는 바꾸지 않습니다."""
        joined = CELL_SEPARATOR_IN_NAME.sub("", value.strip())
        return _fit_to_the_form(_strip_row_number(joined))


def _strip_row_number(name: str) -> str:
    """품목명 앞에 붙어 온 행 번호를 뗍니다. 뗄 것이 없으면 그대로 둡니다.

    전부 떼어 이름이 비면 되돌립니다. "1등급란" 처럼 숫자로 시작하는 멀쩡한 이름을
    잘라 먹는 것보다, 번호가 남는 편이 낫습니다.
    """
    stripped = ROW_NUMBER_PREFIX.sub("", name)
    return stripped if stripped else name


def _fit_to_the_form(name: str) -> str:
    """폼 상한을 넘는 이름은 자릅니다. 길다고 품목을 버리지는 않습니다.

    프롬프트가 이미 20자를 요청하지만 LLM 이 지킨다는 보장이 없고, 넘긴 값이 그대로
    나가면 등록 화면에서 잘립니다. 들어오는 경계에서 한 번 맞춰 둡니다.
    """
    if len(name) <= MAX_ITEM_NAME_LENGTH:
        return name
    logger.info("item name over %d chars, truncated", MAX_ITEM_NAME_LENGTH)
    # 자른 자리가 띄어쓰기면 꼬리 공백이 남습니다. 자른 뒤에 한 번 더 다듬습니다.
    return name[:MAX_ITEM_NAME_LENGTH].rstrip()


class ParsedReceipt(BaseModel):
    """LLM 응답을 검증한 결과. 검증을 통과한 것만 파이프라인 뒷단으로 넘어갑니다."""

    purchased_at: date | None = None
    items: list[ParsedItem] = []

    @field_validator("items")
    @classmethod
    def _drop_unnameable(cls, items: list[ParsedItem]) -> list[ParsedItem]:
        """글자가 없는 이름은 품목이 아닙니다. 등록 폼에 채울 것이 없어 빈 줄만 보입니다.

        빈 문자열도 여기에 포함됩니다. 이미지를 키워서 넣던 시절 LLM 이 빈 문자열을
        돌려준 적이 있고, 저해상도 사진에서는 "10" 이나 "()" 를 품목으로 내보냅니다.
        """
        kept = [item for item in items if HAS_LETTER.search(item.name)]
        if len(kept) < len(items):
            logger.info("dropped %d unnameable items", len(items) - len(kept))
        return kept

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
