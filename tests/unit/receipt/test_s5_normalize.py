"""프롬프트 조립과 LLM 응답 검증.

프롬프트를 만드는 함수가 마스킹의 유일한 통로입니다. 이 파일이 그 불변식을 지킵니다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import pytest

import config
from features.receipt.pipeline import s5_normalize as normalize
from features.receipt.schema import ParsedReceipt
from utils.errors import ResponseValidationError

CARD_LINE = "우리카드:4902************"
OCR_TEXT = f"롯데마트\n{CARD_LINE}\n깐마늘 200g | 2 | 3,180\n[구 매]2026-01-30 18:42"


class _FakeGemini:
    """호출된 프롬프트를 붙잡아 두고 정해진 응답을 돌려줍니다."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompt = ""

    async def complete_json(self, prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        self.prompt = prompt
        return self.payload


def _run(fake: _FakeGemini, monkeypatch: pytest.MonkeyPatch) -> ParsedReceipt:
    monkeypatch.setattr(normalize.gemini, "complete_json", fake.complete_json)
    return asyncio.run(normalize.parse_receipt(OCR_TEXT))


def test_prompt_is_masked_before_it_leaves() -> None:
    """마스킹을 거치지 않은 경로가 생기면 개인정보가 외부로 나갑니다."""
    prompt = normalize.build_prompt(OCR_TEXT)

    assert CARD_LINE not in prompt
    assert "4902" not in prompt
    assert "깐마늘 200g" in prompt
    assert "2026-01-30" in prompt


def test_placeholder_is_replaced() -> None:
    """자리표시자가 남으면 LLM 이 영수증 대신 자리표시자를 읽습니다."""
    prompt = normalize.build_prompt(OCR_TEXT)

    assert normalize.OCR_TEXT_PLACEHOLDER not in prompt


def test_missing_prompt_version_names_what_exists() -> None:
    with pytest.raises(FileNotFoundError) as error:
        normalize.load_prompt(99)

    assert "parse-receipt-v1.md" in str(error.value)


def test_valid_response_becomes_a_parsed_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGemini(
        {
            "purchased_at": "2026-01-30",
            "items": [
                {"name": "깐마늘", "is_food": True},
                {"name": "섬유유연제", "is_food": False},
            ],
        }
    )

    parsed = _run(fake, monkeypatch)

    assert parsed.purchased_at == date(2026, 1, 30)
    assert [item.name for item in parsed.items] == ["깐마늘", "섬유유연제"]
    assert [item.is_food for item in parsed.items] == [True, False]


def test_unreadable_date_becomes_null_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """구매일을 못 읽어도 등록을 막지 않습니다. 사용자가 확인 화면에서 채웁니다."""
    parsed = _run(_FakeGemini({"purchased_at": "몰라요", "items": []}), monkeypatch)

    assert parsed.purchased_at is None
    assert parsed.items == []


def test_response_that_breaks_the_schema_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """검증 없이 다음 단계로 넘기면 어디서 깨졌는지 알 수 없게 됩니다."""
    with pytest.raises(ResponseValidationError):
        _run(_FakeGemini({"items": [{"name": "깐마늘"}]}), monkeypatch)


def test_ocr_text_never_reaches_the_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """원문에는 개인정보가 섞여 들어옵니다. 로그로 새면 마스킹이 무의미해집니다."""
    with caplog.at_level(logging.DEBUG):
        _run(_FakeGemini({"purchased_at": None, "items": []}), monkeypatch)

    assert CARD_LINE not in caplog.text
    assert "4902" not in caplog.text
    assert "깐마늘" not in caplog.text


def test_prompt_version_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """버전을 고정할 수 있어야 A/B 측정이 됩니다. 기존 파일을 고치지 않고 새 버전을 만듭니다."""
    monkeypatch.setenv("RECEIPT_PROMPT_VERSION", "1")
    config.get_settings.cache_clear()
    assert "주류도 식품으로 봅니다" in normalize.build_prompt(OCR_TEXT)

    monkeypatch.setenv("RECEIPT_PROMPT_VERSION", "2")
    config.get_settings.cache_clear()
    assert "주류는 false입니다" in normalize.build_prompt(OCR_TEXT)
