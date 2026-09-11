"""6축 맛 벡터. 값이 없는 축을 계산에서 빼는지, 축 순서가 A 트랙과 같은지 봅니다."""

from __future__ import annotations

import pytest

from features.recommend.engine import taste

#: 온보딩이 지금 채우는 앞 3축만 있는 사용자. 뒤 3축은 아직 물어보지 않습니다.
THREE_AXIS = (1.0, 0.5, 0.0, None, None, None)
CENTER = (0.5,) * taste.AXIS_COUNT


def test_axis_order_follows_the_data_track() -> None:
    """축 순서는 A 트랙 ingest/flavor.py 의 AXES 와 같아야 합니다."""
    assert taste.FLAVOR_AXES == ("매움", "짠맛", "단맛", "신맛", "감칠맛", "기름짐")
    assert taste.AXIS_COUNT == 6


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, (None,) * 6),
        ([0.1, 0.2, 0.3], (0.1, 0.2, 0.3, None, None, None)),
        ([0.1] * 6, (0.1,) * 6),
        ([0.1] * 8, (0.1,) * 6),
    ],
)
def test_as_vector_always_gives_six_axes(
    given: list[float] | None, expected: tuple[float | None, ...]
) -> None:
    assert taste.as_vector(given) == expected


def test_shared_axes_are_the_ones_both_sides_know() -> None:
    user = (0.1, None, 0.3, None, 0.5, None)
    recipe = (0.1, 0.2, None, 0.4, 0.5, 0.6)

    assert taste.shared_axes(user, recipe) == (0, 4)


def test_missing_axes_are_dropped_not_filled_with_zero() -> None:
    """뒤 3축이 없는 사용자와 6축 레시피의 유사도는 앞 3축만으로 잰 값과 같아야 합니다."""
    recipe = (1.0, 0.5, 0.0, 0.9, 0.1, 0.8)
    only_three = taste.centered_cosine(THREE_AXIS, recipe, CENTER)
    trimmed = taste.centered_cosine(
        THREE_AXIS, (1.0, 0.5, 0.0, None, None, None), (0.5, 0.5, 0.5, None, None, None)
    )

    assert only_three == pytest.approx(trimmed)


def test_zero_and_none_are_different() -> None:
    """신맛 0 은 '안 시다', None 은 '모른다'. 0 으로 채우면 모르는 축이 취향이 됩니다."""
    recipe = (0.5, 0.5, 0.5, 1.0, 0.5, 0.5)
    knows_sour_dislikes = (0.5, 0.5, 0.5, 0.0, 0.5, 0.5)

    unknown = taste.centered_cosine(THREE_AXIS, recipe, CENTER)
    disliked = taste.centered_cosine(knows_sour_dislikes, recipe, CENTER)

    assert unknown is None
    assert disliked == pytest.approx(0.0)


def test_centering_is_required() -> None:
    """코퍼스 평균이 없으면 계산하지 않습니다. 중심화 없는 코사인은 전부 비슷하다고 답합니다."""
    assert taste.centered_cosine(THREE_AXIS, (1.0, 0.5, 0.0, None, None, None), None) is None


def test_no_shared_axis_is_unmeasurable() -> None:
    user = (0.9, None, None, None, None, None)
    recipe = (None, 0.9, None, None, None, None)

    assert taste.centered_cosine(user, recipe, CENTER) is None


def test_user_at_the_corpus_mean_has_no_direction() -> None:
    assert taste.centered_cosine(CENTER, (0.9, 0.1, 0.1, None, None, None), CENTER) is None


def test_near_neutral_user_is_damped_toward_half() -> None:
    """전부 '보통' 인 사용자의 잡음이 방향으로 전폭 반영되면 안 됩니다."""
    mean = (0.47, 0.46, 0.58, None, None, None)
    user = (0.5, 0.5, 0.5, None, None, None)
    recipe = (0.9, 0.1, 0.1, None, None, None)

    full = taste.centered_cosine(user, recipe, mean)
    damped = taste.centered_cosine(user, recipe, mean, min_norm=0.25)

    assert full is not None and damped is not None
    assert abs(damped - 0.5) < abs(full - 0.5)


def test_dominant_axis_reports_direction() -> None:
    """둘 다 평균 아래여도 곱은 양수입니다. 방향을 따로 받아야 문구가 뒤집히지 않습니다."""
    likes = taste.dominant_axis(
        (1.0, 0.5, 0.5, None, None, None), (0.9, 0.5, 0.5, None, None, None), CENTER
    )
    avoids = taste.dominant_axis(
        (0.0, 0.5, 0.5, None, None, None), (0.1, 0.5, 0.5, None, None, None), CENTER
    )

    assert likes == ("매움", True)
    assert avoids == ("매움", False)
