"""맛 척도를 이름 있는 칸으로 받는 계약.

## 왜 배열을 버렸는가

우리 계약은 `[매움, 짠맛, 단맛]` 0~4 이고 화면은 `[짠맛, 단맛, 매운맛]` 1~5 입니다.
배열로 받으면 순서를 달리 보내도 값이 세 개이고 범위만 맞으면 통과합니다. 실측으로
125조합을 넣어 보면 61개는 범위에서 거부되고 **나머지 64개는 축이 밀린 채 통과**하며,
우연히 맞는 경우는 없습니다.

이름이 붙으면 순서라는 것이 사라집니다. 빠진 칸은 이름으로 잡히고 모르는 칸은
`extra="forbid"` 가 막습니다.

## 기존 `scales` 도 계속 받습니다

우리 시뮬과 검사가 그 모양으로 쓰고 있습니다. 둘 다 오면 거부합니다 — 어느 쪽이
사용자의 답인지 알 수 없는데 조용히 하나를 고르면 나머지가 표시 없이 버려집니다.
"""

from __future__ import annotations

import pydantic
import pytest

from features.recommend.engine.persona import SCALE_AXIS_COUNT
from features.recommend.engine.taste import FLAVOR_AXES
from features.recommend.schema import CLIENT_SCALE_MAX, CLIENT_SCALE_MIN, OnboardingIn, TasteIn


def test_named_values_land_on_our_axis_order() -> None:
    """화면의 이름 -> 우리 계약 [매움, 짠맛, 단맛] 0~4."""
    got = TasteIn(salty=3, sweet=2, spicy=5).to_scales()
    assert got == [4, 2, 1]


def test_the_order_of_the_keys_does_not_matter() -> None:
    """이름이 붙어 있으면 순서가 의미를 갖지 않습니다. 이것이 배열을 버린 이유입니다."""
    first = OnboardingIn(picks=[0], taste_preferences={"salty": 3, "sweet": 2, "spicy": 5})
    second = OnboardingIn(picks=[0], taste_preferences={"spicy": 5, "sweet": 2, "salty": 3})
    assert first.scales == second.scales == [4, 2, 1]


def test_the_array_form_still_works_and_agrees() -> None:
    """시뮬·검사가 쓰는 모양입니다. 같은 답이면 같은 값이 나와야 합니다."""
    named = OnboardingIn(picks=[0], taste_preferences={"salty": 3, "sweet": 2, "spicy": 5})
    array = OnboardingIn(picks=[0], scales=[4, 2, 1])
    assert named.scales == array.scales


def test_sending_both_is_rejected() -> None:
    """어느 쪽이 답인지 알 수 없습니다. 조용히 하나를 고르면 나머지가 표시 없이 사라집니다."""
    with pytest.raises(pydantic.ValidationError, match="하나만"):
        OnboardingIn(
            picks=[0], scales=[1, 1, 1], taste_preferences={"salty": 1, "sweet": 1, "spicy": 1}
        )


def test_neither_is_allowed_because_picks_alone_can_carry_the_taste() -> None:
    """고른 음식이 있으면 엔진이 척도를 쓰지 않습니다 (결정 D-29)."""
    parsed = OnboardingIn(picks=[0])
    assert parsed.scales is None


@pytest.mark.parametrize("bad", [CLIENT_SCALE_MIN - 1, CLIENT_SCALE_MAX + 1])
def test_client_range_is_enforced_per_axis(bad: int) -> None:
    """칸마다 따로 봅니다. 배열이면 어느 칸이 틀렸는지도 알기 어렵습니다."""
    with pytest.raises(pydantic.ValidationError):
        OnboardingIn(picks=[0], taste_preferences={"salty": bad, "sweet": 1, "spicy": 1})


def test_an_unknown_axis_is_rejected() -> None:
    """신맛은 화면이 묻지 않습니다. 받아 주면 어디에도 안 들어가고 조용히 사라집니다."""
    with pytest.raises(pydantic.ValidationError):
        OnboardingIn(picks=[0], taste_preferences={"salty": 1, "sweet": 1, "spicy": 1, "sour": 3})


def test_a_missing_axis_is_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        OnboardingIn(picks=[0], taste_preferences={"salty": 1, "sweet": 1})


def test_positional_copying_would_have_been_wrong_in_every_case() -> None:
    """배열로 받았을 때 얼마나 틀리는지를 숫자로 고정합니다.

    화면 순서(salty, sweet, spicy) 1~5 를 그대로 배열에 담아 보낸 125조합 가운데
    우연히 우리 계약과 맞는 것은 하나도 없습니다. 이 값이 0 이 아니게 되면
    축 순서나 범위가 바뀐 것이므로 계약 문서를 함께 고쳐야 합니다.
    """
    lucky = 0
    for salty in range(CLIENT_SCALE_MIN, CLIENT_SCALE_MAX + 1):
        for sweet in range(CLIENT_SCALE_MIN, CLIENT_SCALE_MAX + 1):
            for spicy in range(CLIENT_SCALE_MIN, CLIENT_SCALE_MAX + 1):
                want = TasteIn(salty=salty, sweet=sweet, spicy=spicy).to_scales()
                if [salty, sweet, spicy] == want:
                    lucky += 1
    assert lucky == 0


def test_the_axes_route_describes_what_we_accept() -> None:
    client = pytest.importorskip("fastapi.testclient")
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER
    from main import create_app

    header = {INTERNAL_API_KEY_HEADER: get_settings().internal_api_key}
    with client.TestClient(create_app(), headers=header) as http:
        body = http.get("/v1/onboarding/taste-axes").json()

    assert {axis["key"] for axis in body["axes"]} == set(TasteIn.model_fields)
    assert [axis["label"] for axis in body["axes"]] == list(FLAVOR_AXES[:SCALE_AXIS_COUNT])
    assert (body["min"], body["max"]) == (CLIENT_SCALE_MIN, CLIENT_SCALE_MAX)
