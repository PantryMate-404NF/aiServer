"""감점 곱연산. 최근 조리 레시피는 정확히 절반이 되어야 합니다."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from features.recommend.engine.penalty import apply_penalties, avoid_penalty
from features.recommend.stage import (
    RankConfig,
    RecipeCandidate,
    ScoredCandidate,
    UserContext,
    UserHistory,
)


def _scored(recipe: RecipeCandidate, base: float) -> ScoredCandidate:
    return ScoredCandidate(candidate=recipe, missing_ids=(), blocks={}, base_score=base, score=base)


def test_cooked_recipe_is_exactly_halved(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    ctx = make_context(history=UserHistory(cooked_recipe_ids=frozenset({1})))

    assert apply_penalties(_scored(make_recipe(1), 0.8), ctx, cfg).score == pytest.approx(0.4)


def test_recent_exposure_multiplies_by_0_7(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    ctx = make_context(history=UserHistory(recent_recipe_ids=frozenset({1})))

    assert apply_penalties(_scored(make_recipe(1), 0.8), ctx, cfg).score == pytest.approx(0.56)


def test_penalties_multiply_together(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    """뺄셈이 아니라 곱셈입니다. 0.8 - 0.3 - 0.5 = 0.0 이 아니라 0.8 * 0.7 * 0.5 입니다."""
    history = UserHistory(recent_recipe_ids=frozenset({1}), cooked_recipe_ids=frozenset({1}))

    scored = apply_penalties(_scored(make_recipe(1), 0.8), make_context(history=history), cfg)

    assert scored.score == pytest.approx(0.28)
    assert scored.base_score == pytest.approx(0.8)


def test_avoid_penalty_is_proportional_to_ingredient_share(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    recipe = make_recipe(1, essential=[1, 2, 3, 4])
    ctx = make_context(history=UserHistory(avoid_ingredient_ids=frozenset({1})))

    assert avoid_penalty(recipe, ctx, cfg) == pytest.approx(0.5)
    assert apply_penalties(_scored(recipe, 0.8), ctx, cfg).score == pytest.approx(0.4)


def test_avoid_penalty_is_capped(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    """상한이 없으면 기피 비율 50% 부터 감점이 1 을 넘어 점수가 음수가 됩니다."""
    recipe = make_recipe(1, essential=[1, 2, 3, 4])
    ctx = make_context(history=UserHistory(avoid_ingredient_ids=frozenset({1, 2, 3, 4})))

    assert avoid_penalty(recipe, ctx, cfg) == pytest.approx(cfg.avoid_cap)
    assert apply_penalties(_scored(recipe, 1.0), ctx, cfg).score == pytest.approx(0.2)


def test_no_history_leaves_the_score_untouched(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    assert apply_penalties(
        _scored(make_recipe(1), 0.8), make_context(), cfg
    ).score == pytest.approx(0.8)


def test_recipe_without_ingredients_has_no_avoid_penalty(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    ctx = make_context(history=UserHistory(avoid_ingredient_ids=frozenset({1})))

    assert avoid_penalty(make_recipe(1, essential=[]), ctx, cfg) == 0.0
