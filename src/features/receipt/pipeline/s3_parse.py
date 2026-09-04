"""OCR 이 쪼개 놓은 셀을 사람이 읽는 순서의 줄 텍스트로 되돌립니다.

어떤 OCR 엔진을 쓰든 필요한 단계입니다. 엔진은 좌표만 주고 표 구조는 주지 않습니다.
"""

from __future__ import annotations

from config import get_settings
from features.receipt.schema import OcrCell

CELL_SEPARATOR = " | "


def group_lines(cells: list[OcrCell]) -> str:
    """y 중심이 글자 높이의 일정 배수 안이면 같은 줄로 묶고, 줄 안에서는 x 순으로 잇습니다."""
    if not cells:
        return ""

    tolerance_ratio = get_settings().ocr_same_line_height_ratio

    rows: list[list[OcrCell]] = []
    for cell in sorted(cells, key=lambda c: c.y_center):
        if rows:
            previous = rows[-1][-1]
            gap = abs(cell.y_center - previous.y_center)
            if gap <= tolerance_ratio * max(cell.height, previous.height):
                rows[-1].append(cell)
                continue
        rows.append([cell])

    return "\n".join(
        CELL_SEPARATOR.join(
            cell.text.strip() for cell in sorted(row, key=lambda c: c.x_left) if cell.text.strip()
        )
        for row in rows
    )
