"""행 그룹핑. OCR 이 쪼갠 셀을 읽는 순서의 줄로 되돌리는 순수 함수입니다."""

from __future__ import annotations

import pytest

import config
from features.receipt.pipeline.s3_parse import group_lines
from features.receipt.schema import OcrCell


def _cell(
    text: str, x_left: float, y_center: float, height: float = 20, slope: float | None = None
) -> OcrCell:
    return OcrCell(
        text=text, x_left=x_left, y_center=y_center, height=height, score=0.99, slope=slope
    )


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


def test_dense_cells_do_not_chain_into_one_row() -> None:
    """y 가 촘촘히 이어져도 줄이 사슬처럼 엮이면 안 됩니다.

    직전 셀과만 비교하던 시절의 실패입니다. 간격이 매번 임계값 아래라 영수증 한 장이
    통째로 한 줄이 되고, LLM 은 구조가 사라진 덩어리를 받아 품목을 몇 개만 건집니다.
    """
    cells = [_cell(f"항목{index}", 30, 30 + index * 10) for index in range(8)]

    assert len(group_lines(cells).split("\n")) > 1


def test_tilted_receipt_keeps_its_rows_together() -> None:
    """비스듬히 놓인 영수증. 보정이 없으면 품목명과 가격이 다른 줄로 찢어집니다.

    폭 400px 에 기울기 0.1 이면 한 줄의 좌우 끝 y 가 40px 벌어집니다. 글자 높이 20px 에
    임계값 0.6 이면 12px 이므로 y 를 그대로 보는 방식으로는 절대 한 줄이 되지 않습니다.
    """
    slope = 0.1
    rows = {"깐마늘 200g": 30.0, "합계": 120.0}
    # 기울기는 셀 위치가 아니라 각 상자가 제 윗변에서 재 온 값입니다.
    cells = [
        _cell(text, x_left, y_top + slope * x_left, slope=slope)
        for text, y_top in rows.items()
        for x_left in (30.0, 200.0, 400.0)
    ]

    assert len(group_lines(cells).split("\n")) == len(rows)


def test_steep_slope_is_ignored() -> None:
    """15도를 넘게 누운 영수증은 y 를 밀어서 될 일이 아닙니다. 보정을 포기해야 합니다.

    포기하지 않으면 계단처럼 놓인 셀들이 보정 후 높이가 비슷해져 한 줄로 엮입니다.
    """
    steep = 0.9
    cells = [
        _cell(f"{index}00", 30 + index * 40, 30 + index * 40, slope=steep) for index in range(6)
    ]

    assert group_lines(cells).count("\n") == len(cells) - 1


def test_two_column_layout_does_not_look_tilted() -> None:
    """레이블 열과 값 열이 나뉜 전자영수증. 수평인데 배치 때문에 기울어 보이면 안 됩니다.

    셀 위치로 회귀하던 시절의 유출 사고입니다. 왼쪽 레이블 열은 세로로 길고 값 열은
    위쪽에만 몰려 있어 회귀선이 눕고, 그 보정이 `홍길동(hgd0001)` 을 한 줄 아래 `판매자`
    옆에 붙였습니다. 마스킹은 `공급받는자` 다음 칸을 지우므로 실명이 그대로 LLM 으로
    나갔습니다. 아래 배치는 그 회귀 구현에 넣으면 -13도가 나오는 형태입니다. 상자
    윗변은 전부 수평(0)이므로 원래 줄 그대로 묶여야 합니다.
    """
    labels = {"주문번호": 224.0, "공급받는자": 278.0, "구매내역": 338.0, "판매자": 380.0}
    values = {"2026-04-21-AD00B4": 224.0, "홍길동(hgd0001)": 278.5, "2026-09-01": 338.0}
    cells = [_cell(text, 30.0, y, slope=0.0) for text, y in labels.items()]
    cells += [_cell(text, 656.0, y, slope=0.0) for text, y in values.items()]
    # 품목 행. 이름은 왼쪽, 수량과 금액은 오른쪽에 붙습니다.
    for index in range(20):
        y = 500.0 + index * 40
        cells += [
            _cell(f"품목{index}", 30.0, y, slope=0.0),
            _cell("1개", 560.0, y, slope=0.0),
            _cell("5,416원", 630.0, y, slope=0.0),
        ]
    # 하단 안내문. 왼쪽 열을 더 아래로 늘려 회귀선을 눕히는 역할을 합니다.
    cells += [
        _cell(f"안내문{index}", 30.0, 1360.0 + index * 40, 14.0, slope=0.0) for index in range(8)
    ]

    lines = group_lines(cells).split("\n")

    assert [line for line in lines if "공급받는자" in line] == ["공급받는자 | 홍길동(hgd0001)"]
