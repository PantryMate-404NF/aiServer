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
    assert mask("공급받는자 | 홍길동(hgd0001)") == ""
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


def test_english_cashier_label_is_masked() -> None:
    """탑텐 영수증은 계산원 레이블이 영문입니다. 한글 레이블만 보면 실명이 새어 나갑니다."""
    assert "홍길동" not in mask("CASHIER:홍길동 | 12,900")


def test_truncated_owner_label_is_masked() -> None:
    """OCR 이 '대표:' 의 앞 글자를 잘라 '표:' 만 남겨도 뒤의 이름은 지워야 합니다."""
    assert "홍길동" not in mask("표:홍길동")


def test_two_column_owner_value_is_masked() -> None:
    """레이블 칸과 값 칸이 나뉜 배치. '대표' 를 만나면 다음 칸까지 지웁니다."""
    assert "이영희" not in mask("대표 | 이영희")


def test_standalone_name_above_item_header_is_masked() -> None:
    """레이블 없이 고객명이 줄 앞에 홀로 오는 농협 영수증. 같은 셀의 날짜는 살아야 합니다."""
    text = "김철수 | 2015-11-03 16:31\n상품(코드) | 단가 | 수량 | 금액\n양파 | 3,300"

    masked = mask(text)

    assert "김철수" not in masked
    assert "2015-11-03" in masked
    assert "양파" in masked


def test_standalone_name_below_total_is_masked() -> None:
    """계산원명이 합계 아래 홀로 오는 홈플러스 영수증."""
    text = "상품명 | 단가\n고구마 스틱 | 9,990\n합계 | 9,990\n총 구매수량: | 박민수"

    masked = mask(text)

    assert "박민수" not in masked
    assert "고구마 스틱" in masked


def test_name_like_ingredient_inside_items_survives() -> None:
    """고구마·양배추·오징어는 성씨로 시작하는 세 글자입니다. 품목 영역에서는 지우면 안 됩니다."""
    text = "상품명 | 단가\n고구마 | 2,980\n양배추 | 1,980\n오징어 | 5,900\n합계 | 10,860"

    masked = mask(text)

    assert "고구마" in masked
    assert "양배추" in masked
    assert "오징어" in masked


def test_name_rule_is_off_without_an_item_header() -> None:
    """표 머리글이 없으면 품목 영역을 못 가릅니다. 그때는 이름 규칙을 끕니다.

    켜 두면 머리글이 없는 영수증에서 식재료가 통째로 지워집니다.
    """
    assert "고구마" in mask("고구마 | 2,980\n합계 | 2,980")


def test_date_label_is_not_mistaken_for_a_name() -> None:
    """'구매일' 은 성씨 구 로 시작하는 세 글자지만 날짜 레이블입니다.

    지우면 LLM 이 여러 날짜 중 구매일을 고를 단서를 잃습니다.
    """
    text = "구매일 | 2026-01-30\n상품명 | 단가\n양파 | 1,000\n합계 | 1,000"

    assert "구매일" in mask(text)


def test_register_label_is_masked() -> None:
    """세븐일레븐은 계산원을 REG: 뒤에 적습니다. 다른 레이블만 보면 실명이 새어 나갑니다."""
    assert "정수진" not in mask("P:02-01 CNT:0004 REG:정수진")
