"""OCR 원문에서 개인정보를 지웁니다. 외부 LLM 으로 보내기 전에 반드시 거칩니다.

설계 결정 세 가지가 이 파일의 모양을 정합니다.

1. 줄이 아니라 셀 단위로 지웁니다. 행 그룹핑이 셀을 구분자로 이어 놓기 때문에 줄을
   통째로 지우면 같은 줄의 멀쩡한 값까지 날아갑니다. 실제로 영수증 13장 중 2장에서
   구매일과 개인정보가 한 줄에 있었고, 구매일은 잃으면 안 되는 값입니다.
2. 순수 숫자 패턴으로 지우지 않습니다. 금액과 수량이 같이 지워집니다. 레이블이 붙은
   셀만 고릅니다.
3. 레이블 칸과 값 칸이 좌우로 나뉜 2열 배치는 레이블을 만나면 다음 칸까지 지웁니다.

바코드는 지우지 않습니다. 개인정보가 아니고, 품목명 자리에 바코드만 찍히는 영수증이
있어 지우면 품목을 잃습니다.
"""

from __future__ import annotations

import re

# 이 레이블이 붙은 셀은 통째로 지웁니다. 사람이나 결제수단을 식별할 수 있는 값들입니다.
PII_LABELS = re.compile(
    r"([가-힣]{2}카드|카드번호|카드\s*:|카드밴사"  # 하나카드·국민카드·신용카드
    r"|[승증]인\s*번호|승인No|승인일자|가맹점|매입사"  # OCR 이 '승'을 '증'으로 읽습니다
    r"|회원\s*[:번]|고객\s*번호|고객님"
    r"|계산원|담당자|판매원"
    r"|사업자\s*번호|사업자등록"
    r"|TEL|Tel|전\s*화|대표\s*번호|대표\s*:"
    r"|[Pp][O0o][Ss5]\s*[:\d]"  # POS:1509 / pos2 / p052 — OCR 이 O 를 0 으로 읽습니다
    r"|포인트|적립|마일리지"
    r"|주소\s*:"
    r"|매출전표|할부)",
    re.IGNORECASE,
)

# 레이블 없이 형태만으로 알 수 있는 것들
CARD_NUMBER = re.compile(r"\d{4}[-\s]?\d{2,4}[-\s*]{1,}\*{2,}[-\s*]*\d*")
BIZ_NUMBER = re.compile(r"\d{3}-\d{2}-\d{5}|\d{3}-\d{2}-\*{2,}")
PHONE = re.compile(r"0\d{1,2}[-)]\s?\d{3,4}-\d{4}|0\d{1,2}-\*{3,}-\*{4,}")
MASKED_NAME = re.compile(r"[가-힣]\*+[가-힣]")
STAR_MASK = re.compile(r"\*{3,}")  # 영수증이 이미 가려 놓은 값

# 날짜와 시각은 구매일 추출에 필요하므로 살려냅니다.
# 앞에 숫자나 하이픈이 붙으면 날짜가 아닙니다. 사업자번호 425-11-01320 에서 25-11-01 을
# 날짜로 오인해 가짜 구매일이 만들어진 적이 있습니다. 뒤쪽은 막지 않습니다. OCR 이 날짜와
# 시각을 01/04/2617:41 처럼 붙여 읽는 사례가 있습니다.
DATE = re.compile(r"(?<![\d-])(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{2}[-/.]\d{1,2}[-/.]\d{1,2})")
TIME = re.compile(r"\d{1,2}:\d{2}(:\d{2})?")

# 레이블 칸과 값 칸이 좌우로 나뉘는 2열 배치. 전자영수증에서 흔합니다. 값 칸 자체에는
# 단서가 없어 셀 검사가 잡지 못합니다. 칸 전체가 레이블일 때만 걸리도록 앵커를 겁니다.
# "KT 멤버십 할인 | -100" 의 금액을 날리거나 매장 대표번호를 개인정보로 오인하지
# 않기 위해서입니다.
IDENTITY_LABEL = re.compile(r"^(공급받는자|수취인|주문자|구매자|[가-힣]{0,6}멤버십|회원\s*번호)$")

CELL_SEPARATOR = " | "


def mask(text: str) -> str:
    """OCR 원문을 개인정보가 지워진 텍스트로 바꿉니다. 셀이 다 지워진 줄은 통째로 뺍니다."""
    lines: list[str] = []
    for line in text.splitlines():
        cells: list[str] = []
        drop_next = False
        for cell in (c.strip() for c in line.split("|")):
            if drop_next:
                drop_next = False
                # 날짜가 든 칸은 살립니다. 구매일은 잃으면 안 되는 값입니다.
                if not DATE.search(cell):
                    continue
            if IDENTITY_LABEL.match(cell):
                drop_next = True
                continue
            masked = _mask_cell(cell)
            if masked:
                cells.append(masked)
        if cells:
            lines.append(CELL_SEPARATOR.join(cells))
    return "\n".join(lines)


def _mask_cell(cell: str) -> str:
    """셀 하나를 검사합니다. 지워야 하면 빈 문자열, 날짜가 섞여 있으면 날짜만 남깁니다."""
    dirty = bool(
        PII_LABELS.search(cell)
        or CARD_NUMBER.search(cell)
        or BIZ_NUMBER.search(cell)
        or PHONE.search(cell)
        or MASKED_NAME.search(cell)
        or STAR_MASK.search(cell)
    )
    if not dirty:
        return cell

    # 개인정보와 날짜가 한 셀에 섞인 경우 날짜와 시각만 건져냅니다.
    found_date = DATE.search(cell)
    if found_date is None:
        return ""
    found_time = TIME.search(cell)
    return f"{found_date.group()} {found_time.group()}" if found_time else found_date.group()
