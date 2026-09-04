"""행 그룹핑. OCR 이 쪼갠 셀을 읽는 순서의 줄로 되돌리는 순수 함수입니다."""

from __future__ import annotations

import pytest

import config
from features.receipt.pipeline.s3_parse import group_lines
from features.receipt.schema import OcrCell


def _cell(text: str, x_left: float, y_center: float, height: float = 20) -> OcrCell:
    return OcrCell(text=text, x_left=x_left, y_center=y_center, height=height, score=0.99)


def test_same_row_cells_join_in_x_order() -> None:
    """y 가 가까운 셀은 한 줄로 묶이고 줄 안에서는 왼쪽부터 이어집니다."""
    cells = [
        _cell("3,180", 400, 32),
        _cell("깐마늘 200g", 30, 30),
        _cell("2", 300, 31),
        _cell("합계", 30, 80),
        _cell("27,460", 400, 81),
    ]

    assert group_lines(cells) == "깐마늘 200g | 2 | 3,180\n합계 | 27,460"


def test_empty_input_gives_empty_text() -> None:
    assert group_lines([]) == ""


def test_blank_cells_are_dropped() -> None:
    """OCR 이 공백만 뱉은 셀이 구분자만 남기고 줄을 지저분하게 만들면 안 됩니다."""
    cells = [_cell("깐마늘", 30, 30), _cell("   ", 200, 30), _cell("3,180", 400, 30)]

    assert group_lines(cells) == "깐마늘 | 3,180"


def test_rows_split_when_gap_exceeds_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    """임계값이 줄 분리를 정합니다. 이 값을 바꾸면 이 테스트가 먼저 깨져야 합니다."""
    cells = [_cell("위", 30, 30), _cell("아래", 30, 43)]

    monkeypatch.setenv("OCR_SAME_LINE_HEIGHT_RATIO", "0.6")
    config.get_settings.cache_clear()
    assert group_lines(cells) == "위\n아래"

    monkeypatch.setenv("OCR_SAME_LINE_HEIGHT_RATIO", "0.8")
    config.get_settings.cache_clear()
    assert group_lines(cells) == "위 | 아래"
