"""실제 OCR 엔진으로 파이프라인 전체를 돌립니다.

단위 테스트는 PaddleOCR 을 가짜로 바꾸므로 워커 생성, spawn 방식, 모델 로딩, 프로세스
경계를 넘는 이미지 전달, 원시 반환값 변환이 실제로 맞물리는지는 확인하지 못합니다.
이 파일이 그 구간을 봅니다. LLM 은 유료라 여기서도 가짜를 씁니다.

    uv run pytest -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from dotenv import dotenv_values

import config
from features.receipt import service
from features.receipt.pipeline import s2_ocr, s3_parse, s4_mask, s5_normalize
from features.receipt.pipeline.s1_preprocess import to_ocr_input
from features.receipt.schema import ParsedItem, ParsedReceipt
from infra import gemini
from utils.errors import AppError

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
# 단계별 출력에서 볼 이미지를 밖에서 지정하는 환경변수입니다. 파일 하나 또는 디렉터리입니다.
RECEIPT_IMAGE_ENV = "RECEIPT_IMAGE"
# 디렉터리를 받았을 때 고르는 파일. 정답 셋 이미지는 r01 부터 번호가 붙어 있고,
# 제외본은 _excluded/ 아래 x 번호라 이 패턴에 걸리지 않습니다.
RECEIPT_GLOBS = ("r*.jpg", "r*.jpeg", "r*.png")
# 단계별 출력에서 실제 LLM 을 부를 때 지키는 호출 간격. 무료 티어 상한 분당 15회에서
# 여유 1회를 뺀 값입니다. 병렬로 던지면 429 가 납니다.
LLM_INTERVAL_SEC = 60.0 / 14
# .env.example 이 자리만 잡아 둔 값입니다. 이 값이면 키가 없는 것으로 봅니다.
KEY_PLACEHOLDER = "REPLACE_ME"
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


def _receipt_inputs() -> list[tuple[bytes, str]]:
    """볼 이미지를 고릅니다. 환경변수로 실제 영수증 하나 또는 폴더 전체를 지정합니다.

    정답 셋 이미지에는 개인정보가 있어 저장소에 둘 수 없습니다. 경로를 밖에서 받으면
    커밋 가능한 합성 영수증을 기본으로 두면서도 실제 사진으로 단계를 볼 수 있습니다.
    폴더를 받으면 한 번 띄운 워커 풀로 전부 돌립니다. 장마다 풀을 새로 올리면 모델
    로딩이 그 수만큼 반복됩니다.
    """
    raw = os.environ.get(RECEIPT_IMAGE_ENV)
    if not raw:
        return [(_synthetic_receipt(), "합성 영수증")]
    path = Path(raw).expanduser()
    if path.is_file():
        return [(path.read_bytes(), path.name)]
    files = sorted(f for pattern in RECEIPT_GLOBS for f in path.glob(pattern))
    assert files, f"{path} 에 r*.jpg / r*.jpeg / r*.png 가 없습니다"
    return [(f.read_bytes(), f.name) for f in files]


def _real_gemini_key() -> str | None:
    """단계별 출력에서 실제 LLM 을 부를 때 쓸 키입니다. 없으면 None 입니다.

    conftest 가 모든 테스트에 가짜 키를 넣고 .env 로딩도 끕니다. 그래서 .env 를 직접
    읽습니다. 이 테스트 하나만 유료 호출을 하며, 값은 어디에도 출력하지 않습니다.
    """
    value = dotenv_values(".env").get("GEMINI_API_KEY") or ""
    return value if value and value != KEY_PLACEHOLDER else None


def test_print_each_step(run_async: Callable[[Any], Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """영수증마다 OCR 원문, 마스킹 결과, LLM 응답 세 가지를 JSON 으로 찍습니다.

    검증이 아니라 눈으로 보는 용도입니다. 이 세 가지 외에는 아무것도 출력하지 않습니다.

        RECEIPT_IMAGE=<파일 또는 폴더> uv run pytest -m integration --no-cov -s -k print_each_step

    폴더를 주면 r*.jpg / r*.jpeg / r*.png 를 번호순으로 전부 돌립니다. `-s` 가 없으면
    pytest 가 출력을 삼킵니다. .env 에 실제 GEMINI_API_KEY 가 있을 때만 LLM 을 부르고,
    없으면 llm_response 가 null 입니다. 유료 호출이라 이 테스트만 그렇게 합니다.
    """
    key = _real_gemini_key()
    if key:
        monkeypatch.setenv("GEMINI_API_KEY", key)
        config.get_settings.cache_clear()
        gemini.get_client.cache_clear()

    inputs = _receipt_inputs()
    for index, (data, source) in enumerate(inputs, start=1):
        record = _run_steps(source, data, run_async, call_llm=bool(key))
        print(json.dumps(record, ensure_ascii=False, indent=2))
        if key and index < len(inputs):
            time.sleep(LLM_INTERVAL_SEC)


def _run_steps(
    source: str, data: bytes, run_async: Callable[[Any], Any], call_llm: bool
) -> dict[str, Any]:
    """영수증 한 장을 통과시켜 세 단계의 결과를 한 레코드로 모읍니다."""
    image = to_ocr_input(data)

    async def read() -> list[Any]:
        async with s2_ocr.slot():
            return await s2_ocr.read(image)

    cells = run_async(read())
    text = s3_parse.group_lines(cells)
    masked = s4_mask.mask(text)
    assert cells, f"{source}: OCR 이 아무것도 읽지 못했습니다"
    assert text.strip(), f"{source}: 줄 묶기 결과가 비었습니다"

    record: dict[str, Any] = {
        "image": source,
        "ocr_text": text.splitlines(),
        "masked_text": masked.splitlines(),
        "llm_response": None,
    }
    if not call_llm:
        return record
    try:
        parsed = run_async(s5_normalize.parse_receipt(text))
    except AppError as error:
        record["llm_response"] = {"error": str(error)}
        return record
    record["llm_response"] = {
        "prompt_version": config.get_settings().receipt_prompt_version,
        "purchased_at": parsed.purchased_at.isoformat() if parsed.purchased_at else None,
        "items": [{"name": item.name, "is_food": item.is_food} for item in parsed.items],
    }
    return record
