"""단계 조립. OCR 과 LLM 을 가짜로 바꿔 순서와 실패 변환만 봅니다."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

import pytest

from features.receipt import service
from features.receipt.schema import OcrCell, ParsedItem, ParsedReceipt, ReceiptResponse
from utils.errors import ExternalServiceError, ImageDecodeError, LlmUnavailableError, OcrEmptyError

RECEIPT_ID = "01K4A7Q3ZV8XG2M5W9R1DTF6HJ"
IMAGE_BYTES = b"pretend this is a jpeg"

CELLS = [
    OcrCell(text="깐마늘 200g", x_left=30, y_center=30, height=20, score=0.9),
    OcrCell(text="3,180", x_left=400, y_center=31, height=20, score=0.8),
]
PARSED = ParsedReceipt(
    purchased_at=date(2026, 1, 30),
    items=[
        ParsedItem(name="깐마늘", is_food=True),
        ParsedItem(name="섬유유연제", is_food=False),
    ],
)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cells: list[OcrCell] | None = None,
    parsed: ParsedReceipt | None = None,
) -> None:
    monkeypatch.setattr(service.s1_preprocess, "to_ocr_input", lambda data: data)

    @asynccontextmanager
    async def _slot() -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(service.s2_ocr, "slot", _slot)

    async def _read(image: object) -> list[OcrCell]:
        return CELLS if cells is None else cells

    async def _parse(text: str) -> ParsedReceipt:
        return PARSED if parsed is None else parsed

    monkeypatch.setattr(service.s2_ocr, "read", _read)
    monkeypatch.setattr(service.s5_normalize, "parse_receipt", _parse)


def _run() -> ReceiptResponse:
    return asyncio.run(service.parse_receipt_image(RECEIPT_ID, IMAGE_BYTES))


def test_non_food_items_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_food 는 응답에 싣지 않는 내부 필터입니다. 걸러낸 뒤의 목록만 나갑니다."""
    _wire(monkeypatch)

    response = _run()

    assert [item.name for item in response.items] == ["깐마늘"]
    assert response.purchased_at == date(2026, 1, 30)
    assert response.receipt_id == RECEIPT_ID


def test_ingredient_id_is_null_until_the_dictionary_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """미매칭 항목을 버리지 않습니다. 사용자가 확인 화면에서 채웁니다."""
    _wire(monkeypatch)

    assert _run().items[0].ingredient_id is None


def test_no_items_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """품목이 0개여도 오류가 아닙니다. 확인 화면에서 수기 입력으로 넘깁니다."""
    _wire(monkeypatch, parsed=ParsedReceipt(purchased_at=None, items=[]))

    response = _run()

    assert response.items == []
    assert response.purchased_at is None


def test_undecodable_image_becomes_ocr_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """디코딩 실패도 OCR_EMPTY 입니다. 계약에 코드가 둘뿐입니다."""
    _wire(monkeypatch)

    def _fail(data: bytes) -> object:
        raise ImageDecodeError("깨진 파일")

    monkeypatch.setattr(service.s1_preprocess, "to_ocr_input", _fail)

    with pytest.raises(OcrEmptyError) as error:
        _run()
    assert error.value.receipt_id == RECEIPT_ID


def test_empty_ocr_result_becomes_ocr_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, cells=[])

    with pytest.raises(OcrEmptyError):
        _run()


def test_llm_failure_becomes_llm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """외부 서비스 실패가 그대로 새어 나가면 500 계약 본문으로 바뀌지 않습니다."""
    _wire(monkeypatch)

    async def _fail(text: str) -> ParsedReceipt:
        raise ExternalServiceError("gemini down")

    monkeypatch.setattr(service.s5_normalize, "parse_receipt", _fail)

    with pytest.raises(LlmUnavailableError) as error:
        _run()
    assert error.value.receipt_id == RECEIPT_ID


def test_timings_are_logged_not_returned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """meta 는 응답에 없습니다. 동시 처리 확인은 이 로그로 합니다."""
    _wire(monkeypatch)

    with caplog.at_level(logging.INFO):
        response = _run()

    assert "ocr_ms=" in caplog.text
    assert "llm_ms=" in caplog.text
    assert RECEIPT_ID in caplog.text
    assert "meta" not in response.model_dump()


def test_decoding_happens_inside_the_capacity_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """자리를 잡기 전에 디코딩하면 대기 요청이 이미지 배열을 들고 줄을 섭니다."""
    events: list[str] = []

    @asynccontextmanager
    async def _slot() -> AsyncIterator[None]:
        events.append("enter")
        yield
        events.append("exit")

    def _decode(data: bytes) -> bytes:
        events.append("decode")
        return data

    async def _read(image: object) -> list[OcrCell]:
        events.append("read")
        return CELLS

    async def _parse(text: str) -> ParsedReceipt:
        return PARSED

    monkeypatch.setattr(service.s2_ocr, "slot", _slot)
    monkeypatch.setattr(service.s1_preprocess, "to_ocr_input", _decode)
    monkeypatch.setattr(service.s2_ocr, "read", _read)
    monkeypatch.setattr(service.s5_normalize, "parse_receipt", _parse)

    _run()

    assert events == ["enter", "decode", "read", "exit"]
