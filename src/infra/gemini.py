"""Gemini 클라이언트. LLM 호출은 이 파일 하나로만 나갑니다.

호출부가 흩어지면 백엔드 교체가 리팩터링이 됩니다. 다른 기능이 GPU 인스턴스를 띄우면
후처리 LLM 을 그쪽으로 옮기기로 되어 있어, 그 경계를 지금부터 한 곳으로 모읍니다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any

import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import get_settings
from utils.errors import ExternalServiceError

logger = logging.getLogger(__name__)

# 같은 영수증이 매번 같게 파싱되어야 합니다.
TEMPERATURE = 0.0
JSON_MIME_TYPE = "application/json"
# 이 코드로 실패한 호출만 다시 시도합니다. 400 이나 인증 실패는 다시 보내도 같습니다.
# 504 는 뺐습니다. 데드라인을 다 쓰고 돌아오는 코드라 다시 보내면 응답 예산을 두 배로
# 쓰고 백엔드 타임아웃 30초를 넘깁니다. 나머지는 1초 안에 돌아옵니다.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503})
MILLISECONDS_PER_SECOND = 1000


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    """클라이언트는 프로세스당 하나만 만듭니다. 타임아웃은 라이브러리 기본값에 맡기지 않습니다."""
    settings = get_settings()
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=settings.llm_timeout_sec * MILLISECONDS_PER_SECOND),
    )


async def complete_json(prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
    """프롬프트와 JSON 스키마를 주면 파싱된 응답을 돌려줍니다.

    프롬프트는 이미 마스킹을 거친 것이어야 합니다. 여기서 또 하면 이중 마스킹이 되고,
    여기에만 두면 프롬프트를 직접 만드는 경로가 우회로가 됩니다.

    스키마를 강제해 파싱 실패를 구조적으로 막습니다. 그래도 형식이 어긋나면 예외로
    올립니다. 값이 없는 채로 다음 단계에 넘기지 않습니다.
    """
    settings = get_settings()
    config = types.GenerateContentConfig(
        response_mime_type=JSON_MIME_TYPE,
        response_schema=response_schema,
        temperature=TEMPERATURE,
    )

    for attempt in range(settings.llm_max_retries + 1):
        try:
            response = await get_client().aio.models.generate_content(
                model=settings.gemini_model, contents=prompt, config=config
            )
        except APIError as error:
            if error.code not in RETRYABLE_STATUS or attempt == settings.llm_max_retries:
                message = f"Gemini 호출이 실패했습니다 (code={error.code})"
                raise ExternalServiceError(message) from error
            delay = settings.llm_backoff_base_sec * (2**attempt)
            logger.warning("gemini retry in %.1fs (code=%s attempt=%d)", delay, error.code, attempt)
            await asyncio.sleep(delay)
            continue
        except httpx.HTTPError as error:
            # 타임아웃과 연결 실패입니다. 다시 보내지 않습니다. 한 번 더 기다리면
            # 백엔드 타임아웃을 넘겨서 어차피 사용자에게 닿지 않습니다.
            raise ExternalServiceError(
                f"Gemini 호출이 끊겼습니다 ({type(error).__name__})"
            ) from error
        return _decode(response.text)

    raise ExternalServiceError("Gemini 재시도가 모두 실패했습니다.")


def _decode(raw: str | None) -> dict[str, Any]:
    """응답 본문을 dict 로 바꿉니다. 원문 데이터가 아니라 LLM 출력이므로 로그에 남겨도 됩니다."""
    text = (raw or "").strip()
    if not text:
        raise ExternalServiceError("Gemini 가 빈 응답을 돌려줬습니다.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        logger.error("gemini response was not json: %s", text[:200])
        raise ExternalServiceError("Gemini 응답을 JSON 으로 읽지 못했습니다.") from error
    if not isinstance(payload, dict):
        raise ExternalServiceError(f"Gemini 응답이 객체가 아닙니다 (type={type(payload).__name__})")
    return payload
