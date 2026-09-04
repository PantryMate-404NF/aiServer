"""실제 OCR 엔진으로 파이프라인 전체를 돌립니다.

단위 테스트는 PaddleOCR 을 가짜로 바꾸므로 워커 생성, spawn 방식, 모델 로딩, 프로세스
경계를 넘는 이미지 전달, 원시 반환값 변환이 실제로 맞물리는지는 확인하지 못합니다.
이 파일이 그 구간을 봅니다. LLM 은 유료라 여기서도 가짜를 씁니다.

    uv run pytest -m integration --no-cov
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import cv2
import numpy as np
import pytest

import config
from features.receipt import service
from features.receipt.pipeline import s2_ocr, s3_parse
from features.receipt.pipeline.s1_preprocess import to_ocr_input
from features.receipt.schema import ParsedItem, ParsedReceipt

pytestmark = pytest.mark.integration

ENV = {
    "DB_HOST": "localhost",
    "DB_NAME": "aiserver_test",
    "DB_USER": "tester",
    "DB_PASSWORD": "secret",
    "INTERNAL_API_KEY": "test-internal-key",
    "GEMINI_API_KEY": "test-gemini-key",
}
RECEIPT_ID = "01K4A7Q3ZV8XG2M5W9R1DTF6HJ"
LINES = ("MILK 3200", "EGG 5900", "TOTAL 9100")


def _synthetic_receipt() -> bytes:
    """정답 셋 이미지에는 개인정보가 있어 커밋할 수 없으므로 그때그때 그립니다."""
    image = np.full((320, 640, 3), 255, np.uint8)
    for index, line in enumerate(LINES):
        cv2.putText(image, line, (30, 80 + index * 90), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 4)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return bytes(buffer.tobytes())


@pytest.fixture(scope="module")
def run_async() -> Iterator[Callable[[Any], Any]]:
    """모델 로딩이 비싸므로 파일 전체가 워커 풀 하나를 나눠 씁니다."""
    with pytest.MonkeyPatch.context() as patch:
        for key, value in ENV.items():
            patch.setenv(key, value)
        config.get_settings.cache_clear()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(s2_ocr.start_pool())
            yield loop.run_until_complete
        finally:
            s2_ocr.shutdown_pool()
            loop.close()


def test_pool_reports_ready_after_warmup(run_async: Callable[[Any], Any]) -> None:
    assert s2_ocr.is_ready() is True


def test_real_ocr_reads_the_image_and_groups_rows(run_async: Callable[[Any], Any]) -> None:
    """워커까지 갔다 온 결과가 OcrCell 로 돌아오고 행으로 묶이는지 봅니다."""

    async def read() -> list[Any]:
        async with s2_ocr.slot():
            return await s2_ocr.read(to_ocr_input(_synthetic_receipt()))

    cells = run_async(read())

    assert cells, "OCR 이 아무것도 읽지 못했습니다"
    assert all(0.0 <= cell.score <= 1.0 for cell in cells)

    text = s3_parse.group_lines(cells)
    assert "MILK" in text
    assert "EGG" in text
    # 각 줄이 따로 잡혀야 합니다. 한 줄로 뭉치면 품목이 서로 섞입니다.
    assert len(text.splitlines()) == len(LINES)


def test_service_assembles_a_contract_response(
    run_async: Callable[[Any], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """실제 OCR 위에 조립까지 얹어 봅니다. LLM 만 가짜입니다."""
    seen: list[str] = []

    async def fake_parse(text: str) -> ParsedReceipt:
        seen.append(text)
        return ParsedReceipt(
            purchased_at=date(2026, 1, 30),
            items=[ParsedItem(name="우유", is_food=True), ParsedItem(name="세제", is_food=False)],
        )

    monkeypatch.setattr(service.s5_normalize, "parse_receipt", fake_parse)

    response = run_async(service.parse_receipt_image(RECEIPT_ID, _synthetic_receipt()))

    assert response.receipt_id == RECEIPT_ID
    assert response.purchased_at == date(2026, 1, 30)
    assert [item.name for item in response.items] == ["우유"]
    assert response.items[0].ingredient_id is None
    # LLM 에는 OCR 을 거친 실제 텍스트가 넘어가야 합니다.
    assert "MILK" in seen[0]
