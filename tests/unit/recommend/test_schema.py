"""요청 계약과 유연한 파서. 백엔드가 보내는 비정형 값이 랭킹 코어까지 닿지 않게 합니다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from features.recommend.schema import FeedbackEventRequest, RecommendRequest, TastePreference

MINIMAL = {"user_id": 7, "pantry_ingredient_ids": [1, 2]}


def test_persona_household_size_is_parsed_from_text() -> None:
    request = RecommendRequest.model_validate({**MINIMAL, "household_size": "4인 가구"})

    assert request.household_size == 4


@pytest.mark.parametrize("raw", ["0인", "없음", -3, None, 0.9])
def test_persona_household_size_floor_is_one(raw: object) -> None:
    """가구원 수를 못 읽어도 요청을 거부하지 않고 1인으로 봅니다."""
    assert RecommendRequest.model_validate({**MINIMAL, "household_size": raw}).household_size == 1


def test_persona_cuisines_are_parsed_from_text() -> None:
    request = RecommendRequest.model_validate({**MINIMAL, "preferred_cuisines": "한식, 양식/중식"})

    assert request.preferred_cuisines == ["한식", "양식", "중식"]


def test_persona_cuisine_list_drops_blanks() -> None:
    raw = ["한식", "", None, "  양식 "]

    assert RecommendRequest.model_validate(
        {**MINIMAL, "preferred_cuisines": raw}
    ).preferred_cuisines == [
        "한식",
        "양식",
    ]


def test_extra_fields_are_ignored() -> None:
    request = RecommendRequest.model_validate(
        {**MINIMAL, "nickname": "요리초보", "app_version": "2.3"}
    )

    assert not hasattr(request, "nickname")
    assert request.user_id == 7


def test_fixtures_all_parse(personas: list[dict[str, Any]]) -> None:
    """12인 Mock 페르소나가 전부 요청 계약을 통과해야 엔진 테스트의 입력이 됩니다."""
    assert len(personas) == 12
    parsed = [RecommendRequest.model_validate(persona) for persona in personas]

    assert {request.user_id for request in parsed} == set(range(1001, 1013))
    assert all(1 <= request.top_k <= 50 for request in parsed)
    assert all(request.household_size >= 1 for request in parsed)


def test_taste_level_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TastePreference(spicy_level=5)


def test_taste_vector_is_scaled_to_unit_interval() -> None:
    """축 순서는 (매움, 짠맛, 단맛). A 트랙 D-11 과 flavor_vec 앞 3축의 순서입니다."""
    assert TastePreference(spicy_level=4, sweet_level=2, salty_level=0).as_vector() == (
        1.0,
        0.0,
        0.5,
    )


def test_top_k_upper_bound() -> None:
    with pytest.raises(ValidationError):
        RecommendRequest.model_validate({**MINIMAL, "top_k": 51})


def test_missing_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RecommendRequest.model_validate({"user_id": 7})


def test_event_type_is_validated() -> None:
    payload = {
        "user_id": 7,
        "request_id": "0f4c9b1e-9a3f-4a4c-8a52-1c1b0d2e3f45",
        "recipe_id": 3,
        "position": 2,
        "event_type": "cook",
        "timestamp": "2026-09-10T12:00:00+09:00",
    }

    assert FeedbackEventRequest.model_validate(payload).event_type == "cook"
    with pytest.raises(ValidationError):
        FeedbackEventRequest.model_validate({**payload, "event_type": "like"})
