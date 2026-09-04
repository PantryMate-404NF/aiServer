"""마스킹한 OCR 텍스트를 LLM 에 보내 품목과 구매일로 정규화합니다.

프롬프트를 만드는 함수가 마스킹의 유일한 통로입니다. 마스킹을 호출부에 맡기면 언젠가
한 경로가 빠지고, 그 경로로 개인정보가 외부로 나갑니다.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config import get_settings
from features.receipt.pipeline.s4_mask import mask
from features.receipt.schema import ParsedReceipt
from infra import gemini
from utils.errors import ResponseValidationError

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_STEM = "parse-receipt-v"
# 중괄호 포맷이 아니라 문자열 치환을 씁니다. 프롬프트에 중괄호가 들어가도 깨지지 않습니다.
OCR_TEXT_PLACEHOLDER = "{ocr_text}"

# v1 과 v2 가 같이 쓰는 스키마입니다. 두 버전은 is_food 판정 문구만 다릅니다.
# 출력 모양이 달라지는 버전을 만들면 스키마도 그 버전과 함께 옮깁니다.
# 개수와 금액은 뽑지 않습니다. 팬트리 등록 폼에 없는 필드입니다.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "purchased_at": {
            "type": "string",
            "nullable": True,
            "description": "구매일 YYYY-MM-DD. 못 찾으면 null",
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "품목명, 최대 20자"},
                    "is_food": {"type": "boolean"},
                },
                "required": ["name", "is_food"],
            },
        },
    },
    "required": ["items"],
}


def build_prompt(ocr_text: str) -> str:
    """OCR 원문을 LLM 에 보낼 프롬프트로 바꿉니다. 마스킹은 여기서만 일어납니다."""
    template = load_prompt(get_settings().receipt_prompt_version)
    return template.replace(OCR_TEXT_PLACEHOLDER, mask(ocr_text))


@cache
def load_prompt(version: int) -> str:
    """프롬프트를 파일에서 읽습니다. 코드에 박으면 문구 한 줄 고치는 데 배포가 필요합니다."""
    path = PROMPT_DIR / f"{PROMPT_STEM}{version}.md"
    if not path.is_file():
        available = sorted(p.name for p in PROMPT_DIR.glob(f"{PROMPT_STEM}*.md"))
        raise FileNotFoundError(f"{path.name} 이 없습니다. 있는 것: {available}")
    return path.read_text(encoding="utf-8")


async def parse_receipt(ocr_text: str) -> ParsedReceipt:
    """OCR 원문에서 구매일과 품목을 뽑습니다. 비식재료를 거르는 것은 호출부의 몫입니다."""
    payload = await gemini.complete_json(build_prompt(ocr_text), RESPONSE_SCHEMA)
    try:
        return ParsedReceipt.model_validate(payload)
    except ValidationError as error:
        logger.error("llm response did not match the schema: %s", payload)
        raise ResponseValidationError("LLM 응답이 기대한 스키마와 다릅니다.") from error
