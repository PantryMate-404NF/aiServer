"""스테이지 사이의 모델. 설정 지문과 레시피 후보의 불변성."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from features.recommend.stage import RankConfig, RecipeCandidate


def test_fingerprint_changes_with_any_weight() -> None:
    """Track C 가 지문으로 가중치를 되살립니다. 값 하나만 달라도 지문이 달라야 합니다."""
    assert RankConfig().fingerprint() == RankConfig().fingerprint()
    assert RankConfig().fingerprint() != RankConfig(w_match=0.30).fingerprint()
    assert RankConfig().fingerprint() != RankConfig(penalty_cooked=0.51).fingerprint()


def test_weights_follow_the_spec_starting_values() -> None:
    assert RankConfig().weights() == {
        "match": 0.29,
        "expiring": 0.15,
        "taste": 0.31,
        "quality": 0.15,
        "ctx": 0.10,
    }


def test_six_axis_flavor_vector_is_cut_to_the_leading_three() -> None:
    """A 트랙의 6축 flavor_vec 을 그대로 받아도 앞 3축만 씁니다."""
    recipe = RecipeCandidate(
        recipe_id=1,
        title="감자 볶음",
        essential_ids=[4],
        all_ids=[4],
        flavor_vec=[0.7, 0.6, 0.2, 0.1, 0.5, 0.3],
    )

    assert recipe.flavor_vec == (0.7, 0.6, 0.2)


def test_recipe_candidate_is_frozen() -> None:
    recipe = RecipeCandidate(
        recipe_id=1,
        title="감자 볶음",
        essential_ids=[4],
        all_ids=[4, 52],
        flavor_vec=[0.5, 0.5, 0.5],
    )

    assert recipe.essential_ids == frozenset({4})
    with pytest.raises(ValidationError):
        recipe.title = "다른 이름"
