"""17개 피처 원값. 키가 전부 있는지, 못 재는 것이 0 이 아니라 None 인지 봅니다."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext, UserHistory
from features.recommend.engine.feature import compute_features, jaccard_idf, skill_fit, time_fit
from features.recommend.enums import FEATURE_KEYS, UNAVAILABLE_FEATURES
from features.recommend.stage import Candidate

CENTER = (0.5,) * 6
CORPUS = CorpusStats(flavor_mean=CENTER)


def _features(
    recipe: RecipeFeature, ctx: UserContext, candidate: Candidate, corpus: CorpusStats = CORPUS
) -> dict[str, float | None]:
    return compute_features(candidate, recipe, ctx, corpus, max_missing=2)


def test_every_feature_key_is_present(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    """ScoredCandidate 검증기가 17개를 전부 요구합니다. 하나라도 빠지면 거기서 터집니다."""
    values = _features(make_recipe(1, essential=[1]), make_context(pantry=[1]), make_candidate(1))

    assert set(values) == set(FEATURE_KEYS)


def test_unavailable_features_are_always_none(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    """수단 자체가 없는 피처는 데이터가 있어도 None 입니다."""
    values = _features(make_recipe(1, essential=[1]), make_context(pantry=[1]), make_candidate(1))

    assert all(values[key] is None for key in UNAVAILABLE_FEATURES)


def test_pending_data_features_turn_on_when_the_data_arrives(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    """요리군·분류·제철은 칸이 비어 있을 때만 None 입니다. 값이 오면 코드를 안 고쳐도 켜집니다."""
    empty = make_recipe(1, essential=[1], cuisine=None, dish_type=None, season_score=None)
    filled = make_recipe(1, essential=[1], cuisine="한식", dish_type="국물", season_score=0.8)
    ctx = make_context(pantry=[1], preferred_cuisines=frozenset({"한식"}))

    before = _features(empty, ctx, make_candidate(1))
    after = _features(filled, ctx, make_candidate(1))

    assert before["f_cuisine"] is None
    assert before["f_season"] is None
    assert after["f_cuisine"] == 1.0
    assert after["f_season"] == pytest.approx(0.8)


def test_ratio_features_are_unmeasurable_without_a_denominator(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    """임박 재료도 선호 재료도 없으면 0 이 아니라 잴 것이 없다는 뜻입니다."""
    values = _features(make_recipe(1, essential=[1]), make_context(pantry=[1]), make_candidate(1))

    assert values["f_expiring"] is None
    assert values["f_ing_pref"] is None
    assert values["f_pantry_use"] == pytest.approx(1.0)


def test_preference_is_measurable_once_the_user_has_liked_something(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    """콜드 사용자 전원에게 0 을 주면 그 피처가 분모만 차지하고 정보를 주지 않습니다."""
    recipe = make_recipe(1, essential=[1, 2, 3, 4])
    ctx = make_context(pantry=[1], history=UserHistory(liked_ingredient_ids=frozenset({1})))

    assert _features(recipe, ctx, make_candidate(1))["f_ing_pref"] == pytest.approx(0.25)


def test_expiring_is_the_share_of_the_expiring_list(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    recipe = make_recipe(1, essential=[1, 2])
    ctx = make_context(pantry=[1, 2], expiring=[1, 3, 4, 5])

    assert _features(recipe, ctx, make_candidate(1))["f_expiring"] == pytest.approx(0.25)


def test_missing_falls_as_the_shopping_list_grows(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    recipe = make_recipe(1, essential=[1, 2, 3])
    ctx = make_context(pantry=[1])

    ready = _features(recipe, ctx, make_candidate(1, missing_count=0))
    short = _features(recipe, ctx, make_candidate(1, missing_count=2))

    assert ready["f_missing"] == pytest.approx(1.0)
    assert short["f_missing"] == pytest.approx(1 / 3)


def test_cooccurrence_needs_cooking_history(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    recipe = make_recipe(1, essential=[1, 2, 3])
    cold = make_context(pantry=[1])
    warm = make_context(
        pantry=[1],
        history=UserHistory(cooked_ingredient_sets=(frozenset({1, 2, 3}), frozenset({9}))),
    )

    assert _features(recipe, cold, make_candidate(1))["f_cooccur"] is None
    assert _features(recipe, warm, make_candidate(1))["f_cooccur"] == pytest.approx(1.0)


def test_time_fit_penalises_overrun() -> None:
    assert time_fit(45, 30) == pytest.approx(0.5)
    assert time_fit(20, 30) == pytest.approx(1.0)
    assert time_fit(20, None) is None
    assert time_fit(None, 30) is None


def test_skill_fit_needs_both_sides() -> None:
    assert skill_fit(0.5, 0.5) == pytest.approx(1.0)
    assert skill_fit(0.9, 0.4) == pytest.approx(0.5)
    assert skill_fit(0.5, None) is None
    assert skill_fit(None, 0.5) is None


def test_jaccard_idf_discounts_common_ingredients() -> None:
    left = frozenset({1, 9})
    right = frozenset({1})

    assert jaccard_idf(left, right, {}) == pytest.approx(0.5)
    assert jaccard_idf(left, right, {1: 0.1, 9: 3.0}) == pytest.approx(0.1 / 3.1)
    assert jaccard_idf(frozenset(), frozenset(), {}) == 0.0


def test_taste_uses_only_the_axes_the_user_knows(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
) -> None:
    """온보딩이 앞 3축만 채워도 맛 피처가 나옵니다. 뒤 3축은 분모에서 빠집니다."""
    recipe = make_recipe(1, essential=[1], flavor=[1.0, 0.5, 0.5, 0.9, 0.2, 0.7])
    ctx = make_context(pantry=[1], taste_vec=[1.0, 0.5, 0.5])

    assert _features(recipe, ctx, make_candidate(1))["f_taste"] == pytest.approx(1.0)
