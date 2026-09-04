"""OCR 워커 풀의 경계. 실제 모델은 통합 테스트에서만 돌립니다."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from features.receipt.pipeline import s2_ocr as ocr
from utils.errors import OcrPoolNotReadyError


class _FakeEngine:
    """PaddleOCR 이 돌려주는 모양만 흉내 냅니다."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def predict(self, image: NDArray[np.uint8]) -> list[dict[str, Any]]:
        return [self._payload]


def test_pool_is_not_ready_before_start() -> None:
    assert ocr.is_ready() is False


def test_extract_cells_without_engine_names_the_failure() -> None:
    """initializer 가 실패한 워커가 조용히 빈 결과를 돌려주면 안 됩니다."""
    with pytest.raises(OcrPoolNotReadyError):
        ocr._extract_cells(np.zeros((4, 4, 3), dtype=np.uint8))


def test_raw_result_becomes_ocr_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    """폴리곤에서 좌측 x, y 중심, 글자 높이를 뽑아냅니다. 행 그룹핑이 이 값에 걸려 있습니다."""
    payload = {
        "rec_texts": ["깐마늘"],
        "rec_polys": [[(30, 20), (150, 22), (150, 44), (30, 42)]],
        "rec_scores": [0.97],
    }
    monkeypatch.setattr(ocr, "_engine", _FakeEngine(payload))

    cells = ocr._extract_cells(np.zeros((4, 4, 3), dtype=np.uint8))

    assert len(cells) == 1
    cell = cells[0]
    assert cell.text == "깐마늘"
    assert cell.x_left == 30
    assert cell.y_center == 32
    assert cell.height == 24
    assert cell.score == pytest.approx(0.97)


def test_missing_scores_default_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """신뢰도는 로그용입니다. 없다고 해서 인식 결과를 버리지 않습니다."""
    payload = {
        "rec_texts": ["합계"],
        "rec_polys": [[(0, 0), (10, 0), (10, 10), (0, 10)]],
    }
    monkeypatch.setattr(ocr, "_engine", _FakeEngine(payload))

    assert ocr._extract_cells(np.zeros((4, 4, 3), dtype=np.uint8))[0].score == 0.0


def test_broken_pool_turns_readiness_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """워커가 죽으면 이후 모든 요청이 실패합니다. 헬스체크가 그 사실을 말해야
    오케스트레이터가 재시작할 근거를 받습니다."""

    def _die(image: NDArray[np.uint8]) -> list[object]:
        raise BrokenProcessPool("worker died")

    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(ocr, "_pool", executor)
        monkeypatch.setattr(ocr, "_is_ready", True)
        monkeypatch.setattr(ocr, "_extract_cells", _die)

        with pytest.raises(OcrPoolNotReadyError):
            asyncio.run(ocr.read(np.zeros((4, 4, 3), dtype=np.uint8)))

        assert ocr.is_ready() is False
