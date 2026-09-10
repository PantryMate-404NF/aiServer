"""프롬프트 조립과 LLM 응답 검증.

프롬프트를 만드는 함수가 마스킹의 유일한 통로입니다. 이 파일이 그 불변식을 지킵니다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import pytest

import config
from features.receipt.pipeline import s5_normalize as normalize
from features.receipt.schema import MAX_ITEM_NAME_LENGTH, ParsedReceipt
from utils.errors import ResponseValidationError

CARD_LINE = "우리카드:4902************"
OCR_TEXT = f"롯데마트\n{CARD_LINE}\n깐마늘 200g | 2 | 3,180\n[구 매]2026-01-30 18:42"


class _FakeGemini:
    """호출된 프롬프트를 붙잡아 두고 정해진 응답을 돌려줍니다."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompt = ""

    async def complete_json(self, prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        self.prompt = prompt
        return self.payload


def _run(fake: _FakeGemini, monkeypatch: pytest.MonkeyPatch) -> ParsedReceipt:
    monkeypatch.setattr(normalize.gemini, "complete_json", fake.complete_json)
    return asyncio.run(normalize.parse_receipt(OCR_TEXT))


def test_prompt_is_masked_before_it_leaves() -> None:
    """마스킹을 거치지 않은 경로가 생기면 개인정보가 외부로 나갑니다."""
    prompt = normalize.build_prompt(OCR_TEXT)

    assert CARD_LINE not in prompt
    assert "4902" not in prompt
    assert "깐마늘 200g" in prompt
    assert "2026-01-30" in prompt


def test_placeholder_is_replaced() -> None:
    """자리표시자가 남으면 LLM 이 영수증 대신 자리표시자를 읽습니다."""
    prompt = normalize.build_prompt(OCR_TEXT)

    assert normalize.OCR_TEXT_PLACEHOLDER not in prompt


def test_missing_prompt_version_names_what_exists() -> None:
    with pytest.raises(FileNotFoundError) as error:
        normalize.load_prompt(99)

    assert "parse-receipt-v1.md" in str(error.value)


def test_valid_response_becomes_a_parsed_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGemini(
        {
            "purchased_at": "2026-01-30",
            "items": [
                {"name": "깐마늘", "is_food": True},
                {"name": "섬유유연제", "is_food": False},
            ],
        }
    )

    parsed = _run(fake, monkeypatch)

    assert parsed.purchased_at == date(2026, 1, 30)
    assert [item.name for item in parsed.items] == ["깐마늘", "섬유유연제"]
    assert [item.is_food for item in parsed.items] == [True, False]


def test_unreadable_date_becomes_null_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """구매일을 못 읽어도 등록을 막지 않습니다. 사용자가 확인 화면에서 채웁니다."""
    parsed = _run(_FakeGemini({"purchased_at": "몰라요", "items": []}), monkeypatch)

    assert parsed.purchased_at is None
    assert parsed.items == []


def test_response_that_breaks_the_schema_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """검증 없이 다음 단계로 넘기면 어디서 깨졌는지 알 수 없게 됩니다."""
    with pytest.raises(ResponseValidationError):
        _run(_FakeGemini({"items": [{"name": "깐마늘"}]}), monkeypatch)


def test_ocr_text_never_reaches_the_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """원문에는 개인정보가 섞여 들어옵니다. 로그로 새면 마스킹이 무의미해집니다."""
    with caplog.at_level(logging.DEBUG):
        _run(_FakeGemini({"purchased_at": None, "items": []}), monkeypatch)

    assert CARD_LINE not in caplog.text
    assert "4902" not in caplog.text
    assert "깐마늘" not in caplog.text


def test_prompt_version_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """버전을 고정할 수 있어야 A/B 측정이 됩니다. 기존 파일을 고치지 않고 새 버전을 만듭니다."""
    monkeypatch.setenv("RECEIPT_PROMPT_VERSION", "1")
    config.get_settings.cache_clear()
    assert "주류도 식품으로 봅니다" in normalize.build_prompt(OCR_TEXT)

    monkeypatch.setenv("RECEIPT_PROMPT_VERSION", "2")
    config.get_settings.cache_clear()
    assert "주류는 false입니다" in normalize.build_prompt(OCR_TEXT)

    monkeypatch.setenv("RECEIPT_PROMPT_VERSION", "3")
    config.get_settings.cache_clear()
    assert "그대로 옮깁니다" in normalize.build_prompt(OCR_TEXT)

    monkeypatch.setenv("RECEIPT_PROMPT_VERSION", "4")
    config.get_settings.cache_clear()
    assert "주류와 담배는 false입니다" in normalize.build_prompt(OCR_TEXT)

    monkeypatch.setenv("RECEIPT_PROMPT_VERSION", "5")
    config.get_settings.cache_clear()
    assert "발급일시" in normalize.build_prompt(OCR_TEXT)


def test_default_prompt_matches_the_ground_truth_rules() -> None:
    """기본 경로가 정답 셋(ocr_poc/eval/ground_truth.json)의 판정 기준과 같아야 합니다.

    이 셋 중 하나라도 빠지면 식재료 판정이 정답 셋과 어긋나 채점이 의미를 잃습니다.
    """
    prompt = normalize.build_prompt(OCR_TEXT)

    assert "주류와 담배는 false입니다" in prompt
    assert "그 자리에서 먹는 완제품은 false입니다" in prompt
    assert "items를 빈 배열로 둡니다" in prompt
    # 전자영수증은 발급일시가 구매일보다 나중입니다. 그걸 쓰면 소비기한 기준일이 밀립니다.
    assert "발급일시" in prompt


def test_default_prompt_forbids_rewriting_item_names() -> None:
    """설정을 건드리지 않은 기본 경로가 교정 금지 버전이어야 합니다.

    LLM 이 이름을 고치면 원문이 사라져 뒷단 사전 매칭이 손댈 것이 없어집니다.
    "챗잎" 이 "깻잎" 으로 바뀌어 오면 어느 쪽이 OCR 이 읽은 값인지 알 수 없습니다.
    """
    prompt = normalize.build_prompt(OCR_TEXT)

    assert "그대로 옮깁니다" in prompt
    assert "고치지 마세요" in prompt


def test_long_item_name_is_trimmed_to_the_form_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """프롬프트가 20자를 요청하지만 LLM 이 지킨다는 보장이 없습니다."""
    long_name = "유기농무항생제특란대란왕란모듬계란한판삼십구"
    fake = _FakeGemini({"purchased_at": None, "items": [{"name": long_name, "is_food": True}]})

    parsed = _run(fake, monkeypatch)

    assert parsed.items[0].name == long_name[:MAX_ITEM_NAME_LENGTH]
    assert len(parsed.items[0].name) == MAX_ITEM_NAME_LENGTH


def test_short_item_name_is_left_alone_but_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """길다고 품목을 버리지 않습니다. 짧은 이름은 공백만 정리합니다."""
    fake = _FakeGemini({"purchased_at": None, "items": [{"name": "  깐마늘 ", "is_food": True}]})

    assert _run(fake, monkeypatch).items[0].name == "깐마늘"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01델몬트 스파게티먼500g", "델몬트 스파게티먼500g"),
        ("02*베로나 엑스트라버진", "베로나 엑스트라버진"),
        ("005P하선정바로먹기좋은장아찌", "하선정바로먹기좋은장아찌"),
        ("(G)피코크'티라미수", "피코크'티라미수"),
        ("(6)피코크 레이디핑", "피코크 레이디핑"),
        ("*레쉬센터 990까대", "레쉬센터 990까대"),
        # 감열지에서 0 이 O 로, 8 이 B 로 바뀌어 읽히는 경우입니다.
        ("0B*호박고구마 1ly", "호박고구마 1ly"),
        ("13* 이마트 각얼음", "이마트 각얼음"),
        ("P고소한검은콩&고칼슘두유", "고소한검은콩&고칼슘두유"),
    ],
)
def test_row_number_prefix_is_stripped(
    raw: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """행 번호는 OCR 이 품목명에 붙여서 읽어 옵니다. 이름이 아니므로 떼어냅니다.

    프롬프트에 맡겼더니 교정 금지 지시와 부딪혀 열 건 넘게 새어 나왔습니다. 규칙이
    분명한 잘라내기는 코드에서 확정합니다.
    """
    fake = _FakeGemini({"purchased_at": None, "items": [{"name": raw, "is_food": True}]})

    assert _run(fake, monkeypatch).items[0].name == expected


@pytest.mark.parametrize(
    "name",
    ["가농 1등급란 24개입", "100% 오렌지주스", "2%우유", "500ml 생수", "양파", "챗잎"],
)
def test_ordinary_names_survive_the_prefix_rule(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """숫자로 시작하는 멀쩡한 이름을 잘라 먹으면 안 됩니다. 오탐이 미탐보다 나쁩니다."""
    fake = _FakeGemini({"purchased_at": None, "items": [{"name": name, "is_food": True}]})

    assert _run(fake, monkeypatch).items[0].name == name


def test_cell_separator_inside_a_name_is_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    """전자영수증에서 두 줄로 꺾인 이름을 LLM 이 구분자째 이어 붙이는 경우입니다.

    프롬프트가 지우라고 해도 지켜지지 않아 검증기가 확정합니다. 꺾인 자리는 단어
    중간이라 공백 없이 이어야 원래 이름이 됩니다. "(50 | g)" 는 "(50g)" 이지 "(50 g)" 가
    아닙니다.
    """
    fake = _FakeGemini(
        {"purchased_at": None, "items": [{"name": "카스텔크림레몬캔디(50 | g)", "is_food": True}]}
    )

    assert _run(fake, monkeypatch).items[0].name == "카스텔크림레몬캔디(50g)"


def test_truncation_does_not_leave_a_trailing_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """20자째가 띄어쓰기면 자른 뒤 꼬리 공백이 남습니다. 공백 정리는 자른 뒤에도 해야 합니다."""
    name = "서울우유 [서울우유] 비요뜨 초코링 미니컵"  # 20번째 글자가 공백입니다
    assert name[MAX_ITEM_NAME_LENGTH - 1] == " "
    fake = _FakeGemini({"purchased_at": None, "items": [{"name": name, "is_food": True}]})

    result = _run(fake, monkeypatch).items[0].name

    assert result == name[:MAX_ITEM_NAME_LENGTH].rstrip()
    assert not result.endswith(" ")


@pytest.mark.parametrize("junk", ["10", "()", "", "   ", "12,000", "***"])
def test_names_without_a_letter_are_dropped(junk: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """글자가 없으면 품목명이 아닙니다. 저해상도 사진에서 LLM 이 이런 조각을 품목으로 냅니다."""
    fake = _FakeGemini(
        {
            "purchased_at": None,
            "items": [{"name": junk, "is_food": True}, {"name": "깐마늘", "is_food": True}],
        }
    )

    assert [item.name for item in _run(fake, monkeypatch).items] == ["깐마늘"]


@pytest.mark.parametrize("name", ["무", "0누21", "HERB THYME", "2%우유"])
def test_names_with_any_letter_survive(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """한 글자 품목(무)과 OCR 이 뭉갠 이름은 남깁니다. 지우면 사용자가 고칠 기회도 사라집니다."""
    fake = _FakeGemini({"purchased_at": None, "items": [{"name": name, "is_food": True}]})

    assert _run(fake, monkeypatch).items[0].name == name
