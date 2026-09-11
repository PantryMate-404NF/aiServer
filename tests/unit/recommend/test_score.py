"""② Ranking. Zero-Drop 정규화와 곱연산 감점."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext, UserHistory
from features.recommend.engine.score import (
    avoid_penalty,
    penalty_factor,
    score_all,
    score_candidate,
    weighted_score,
)
from features.recommend.enums import FEATURE_KEYS
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate

CENTER = (0.5,) * 6
CORPUS = CorpusStats(flavor_mean=CENTER)


def test_none_features_leave_both_numerator_and_denominator() -> None:
    """못 잰 피처를 0 으로 채우면 결측이 감점이 되고, 분모를 고정하면 결측이 불리해집니다."""
    weights = {"a": 0.6, "b": 0.4}

    both = weighted_score({"a": 1.0, "b": 0.5}, weights)
    only_a = weighted_score({"a": 1.0, "b": None}, weights)

    assert both == pytest.approx(0.8)
    assert only_a == pytest.approx(1.0)


def test_zero_weight_features_do_not_move_the_score() -> None:
    """가중치 0 인 피처는 로그에는 남지만 점수에는 들어가지 않습니다."""
    weights = {"a": 0.5, "b": 0.0}

    assert weighted_score({"a": 0.4, "b": 1.0}, weights) == pytest.approx(0.4)


def test_no_measurable_feature_gives_zero() -> None:
    assert weighted_score(dict.fromkeys(FEATURE_KEYS), {"f_coverage": 0.24}) == 0.0


def test_cooked_recipe_is_exactly_halved(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> None:
    ctx = make_context(history=UserHistory(cooked_recipe_ids=frozenset({1})))

    assert penalty_factor(1, make_recipe(1), ctx, policy) == pytest.approx(0.5)


def test_penalties_multiply_together(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> None:
    """뺄셈이 아니라 곱셈입니다. 1 - 0.3 - 0.5 = 0.2 가 아니라 0.7 곱하기 0.5 입니다."""
    history = UserHistory(recent_recipe_ids=frozenset({1}), cooked_recipe_ids=frozenset({1}))

    assert penalty_factor(
        1, make_recipe(1), make_context(history=history), policy
    ) == pytest.approx(0.35)


def test_avoid_penalty_is_capped(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> None:
    """상한이 없으면 기피 비율 50% 부터 감점이 1 을 넘어 점수가 음수가 됩니다."""
    recipe = make_recipe(1, essential=[1, 2, 3, 4])
    half = make_context(history=UserHistory(avoid_ingredient_ids=frozenset({1, 2})))
    everything = make_context(history=UserHistory(avoid_ingredient_ids=frozenset({1, 2, 3, 4})))

    assert avoid_penalty(recipe, half, policy) == pytest.approx(0.8)
    assert avoid_penalty(recipe, everything, policy) == pytest.approx(policy.avoid_cap)
    assert penalty_factor(1, recipe, everything, policy) == pytest.approx(0.2)


def test_scored_candidate_carries_every_feature(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
    policy: RankingPolicy,
) -> None:
    scored = score_candidate(
        make_candidate(1, coverage=0.75, missing_count=1, missing_ids=[9]),
        make_recipe(1, essential=[1, 9]),
        make_context(pantry=[1]),
        CORPUS,
        policy,
    )

    assert set(scored.features) == set(FEATURE_KEYS)
    assert scored.coverage == pytest.approx(0.75)
    assert scored.missing_ids == [9]
    assert 0.0 <= scored.score <= 1.0
    assert 0.0 <= scored.penalty <= 1.0


def test_penalty_is_recorded_so_the_raw_score_can_be_recovered(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
    policy: RankingPolicy,
) -> None:
    """로그에 감점 계수가 남아야 나중에 감점 전 점수를 되계산할 수 있습니다."""
    ctx = make_context(pantry=[1], history=UserHistory(cooked_recipe_ids=frozenset({1})))
    clean = make_context(pantry=[1])
    candidate = make_candidate(1, coverage=1.0)
    recipe = make_recipe(1, essential=[1])

    cooked = score_candidate(candidate, recipe, ctx, CORPUS, policy)
    fresh = score_candidate(candidate, recipe, clean, CORPUS, policy)

    assert cooked.penalty == pytest.approx(0.5)
    # score 는 소수 6자리로 반올림해 로그에 싣습니다. 그 자리만큼 여유를 둡니다.
    assert cooked.score == pytest.approx(fresh.score * 0.5, abs=1e-6)


def test_score_all_sorts_by_score_then_recipe_id(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
    policy: RankingPolicy,
) -> None:
    candidates = [make_candidate(i, coverage=1.0 - i / 10) for i in range(5)]
    recipes = {i: make_recipe(i, essential=[i]) for i in range(5)}

    scored = score_all(candidates, recipes, make_context(pantry=range(5)), CORPUS, policy)

    assert [item.recipe_id for item in scored] == [0, 1, 2, 3, 4]
    assert [item.score for item in scored] == sorted((i.score for i in scored), reverse=True)


def test_missing_recipe_feature_does_not_drop_the_candidate(
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
    policy: RankingPolicy,
) -> None:
    """제외는 ① 에서만 합니다. ② 가 후보를 빼면 왜 빠졌는지를 두 곳에서 찾아야 합니다."""
    scored = score_all([make_candidate(7)], {}, make_context(), CORPUS, policy)

    assert [item.recipe_id for item in scored] == [7]
