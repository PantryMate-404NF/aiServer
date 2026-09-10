"""OCR 이 쪼개 놓은 셀을 사람이 읽는 순서의 줄 텍스트로 되돌립니다.

어떤 OCR 엔진을 쓰든 필요한 단계입니다. 엔진은 좌표만 주고 표 구조는 주지 않습니다.
"""

from __future__ import annotations

from statistics import median

from config import get_settings
from features.receipt.schema import OcrCell

CELL_SEPARATOR = " | "

# 기울기를 재려면 각도를 들고 온 긴 상자가 이만큼은 있어야 합니다. 적으면 수평으로 봅니다.
MIN_CELLS_FOR_SLOPE = 4
# 손에 들고 찍은 영수증이 흔히 눕는 범위인 15도의 기울기입니다. 이보다 가파르면 y 를
# 밀어서 될 일이 아닙니다. 41도로 누운 영수증에서 보정 유무가 결과를 바꾸지 못했습니다.
MAX_BASELINE_SLOPE = 0.27


def group_lines(cells: list[OcrCell]) -> str:
    """같은 줄의 셀을 묶고, 줄 안에서는 x 순으로 잇습니다.

    y 를 그대로 쓰지 않고 기울기를 먼저 걷어냅니다. 비스듬히 놓인 영수증은 한 줄의 좌우
    끝 y 가 크게 벌어져서, 보정 없이 y 만 보면 품목명과 가격이 다른 줄로 찢어집니다.
    """
    if not cells:
        return ""

    tolerance_ratio = get_settings().ocr_same_line_height_ratio
    slope = _baseline_slope(cells)

    rows: list[list[OcrCell]] = []
    # 행마다 그 행이 시작된 기준 높이입니다. 직전 셀이 아니라 이 값과 비교해야 합니다.
    # 직전 셀과 비교하면 셀이 촘촘히 이어질 때 임계값을 계속 통과해서 영수증 전체가
    # 사슬처럼 한 줄로 엮입니다.
    row_levels: list[float] = []
    for cell, level in sorted(
        ((cell, cell.y_center - slope * cell.x_left) for cell in cells),
        key=lambda pair: pair[1],
    ):
        if rows:
            tolerance = tolerance_ratio * max(cell.height, rows[-1][0].height)
            if abs(level - row_levels[-1]) <= tolerance:
                rows[-1].append(cell)
                continue
        rows.append([cell])
        row_levels.append(level)

    return "\n".join(
        CELL_SEPARATOR.join(
            cell.text.strip() for cell in sorted(row, key=lambda c: c.x_left) if cell.text.strip()
        )
        for row in rows
    )


def _baseline_slope(cells: list[OcrCell]) -> float:
    """글자 줄이 오른쪽으로 갈수록 얼마나 내려가는지. 못 재면 0 입니다.

    각 상자가 들고 온 윗변 기울기의 중앙값입니다. 셀 위치로 회귀하지 않습니다. 레이블
    열이 세로로 길고 값 열이 위쪽에만 몰린 2열 전자영수증에서는 배치가 회귀선을 눕혀,
    수평인 영수증에 -8도가 잡혔습니다. 그 가짜 보정이 값 칸을 한 줄 아래 레이블에 붙여
    개인정보 마스킹을 빗나가게 했습니다. 상자 윗변은 배치와 무관합니다.
    """
    measured = [cell.slope for cell in cells if cell.slope is not None]
    if len(measured) < MIN_CELLS_FOR_SLOPE:
        return 0.0
    slope = median(measured)
    return 0.0 if abs(slope) > MAX_BASELINE_SLOPE else slope
