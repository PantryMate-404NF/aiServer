"""개인정보 마스킹. 여기가 뚫리면 개인정보가 외부 LLM 으로 나갑니다.

PoC 에서 실제 OCR 출력으로 잡아낸 검증 케이스 24개를 그대로 옮겼습니다. 정규식은
실제 출력에 돌려 봐야 빠진 것이 나오므로, 케이스를 줄이면 그때 잡은 것을 다시 놓칩니다.
"""

from __future__ import annotations

from features.receipt.pipeline.s4_mask import mask


def test_purchase_date_survives_when_it_shares_a_line_with_pii() -> None:
    """구매일과 개인정보가 한 줄에 있는 실제 사례입니다. 날짜는 반드시 살아야 합니다."""
    assert mask("[구 매]2025-12-25 18:42 | POS:1509-0607") == "[구 매]2025-12-25 18:42"
    assert mask("2021/10/31김*숙 | NO:14522") == "2021/10/31 | NO:14522"
    # OCR 이 POS 를 p052 로 뭉갠 사례. 계산원 이름은 지우고 구매일은 살립니다.
    assert mask("01/04/2617:41p052박점숙 | #000153#") == "01/04/26 17:41 | #000153#"
    assert "2020-09-20" in mask("2020-09-20일:001:0297 | 기본사원")


def test_item_lines_are_untouched() -> None:
    """품목 줄을 건드리면 재현율이 그대로 떨어집니다."""
    item = "깐마늘 200g | 2 | 3,180"
    assert mask(item) == item
    assert mask("합계 | 27,460") == "합계 | 27,460"
    # 세금 정보는 품목 판단에 무해합니다.
    assert "6,436" in mask("과세 매출 | 6,436")


def test_barcodes_are_kept() -> None:
    """상품코드이고, 품목명 자리에 바코드만 찍히는 영수증이 있습니다."""
    assert mask("8801005638654 | 1,380 | 2 | 2,760").startswith("8801005638654")


def test_labelled_pii_cells_are_removed() -> None:
    assert mask("우리카드:4902************") == ""
    assert mask("사업자번호:127-82-*****") == ""
    assert mask("회원:2010190034*** 박*분님") == ""
    assert mask("계산원:윤*아 | 대표번호:02)358-8546") == ""
    assert mask("5181-8500-****-885* 45") == ""
    # 별표가 앞에 오는 형태와 카드사명
    assert mask("0005 하나카드(토스뱅크 ***838* | 18700302 | 100,250") == "18700302 | 100,250"
    # OCR 이 '승'을 '증'으로 읽은 사례
    assert mask("증인번호 | 0(매입사: 국민카드)") == ""


def test_two_column_layout_drops_the_value_next_to_the_label() -> None:
    """전자영수증은 레이블 칸과 값 칸이 좌우로 나뉘어 값 칸에 단서가 없습니다."""
    assert mask("공급받는자 | 김민경(kmk9259)") == ""
    assert mask("다이소멤버십 | 2002516687") == ""
    assert mask("회원번호 | 2010190034") == ""
    # 레이블 다음 칸이 구매일이면 살립니다.
    assert mask("공급받는자 | 2026-04-21") == "2026-04-21"


def test_label_lookalikes_are_not_removed() -> None:
    """레이블처럼 보이지만 아닌 것들입니다. 지우면 금액과 매장 연락처를 잃습니다."""
    assert mask("K 멤버십말인 | -100") == "K 멤버십말인 | -100"
    assert "1599-2211" in mask("멤버십콜센터:1599-2211")


def test_numbers_inside_business_ids_are_not_read_as_dates() -> None:
    """사업자번호에서 가짜 구매일이 만들어져 구매일 정확도가 92.9% 로 떨어진 적이 있습니다."""
    assert mask("사업자:425-11-01320") == ""
    assert mask("대표:김기호 213-81-52063") == ""
    assert "04-21" not in mask("국민은행 | 075602-04-21****")
