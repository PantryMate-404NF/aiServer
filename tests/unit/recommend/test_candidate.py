"""Stage 1. 알레르기 하드컷과 후보 부족 시의 완화 순서를 봅니다."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from features.recommend.engine.candidate import (
    FALLBACK_NONE,
    FALLBACK_POPULARITY,
    FALLBACK_RELAX_MISSING,
    FALLBACK_SUBSTITUTE,
    is_eligible,
    missing_ids,
    retrieve,
    select_candidates,
)
from features.recommend.schema import RankConfig, RecipeCandidate, UserContext, UserHistory


def _persona(personas: list[dict[str, Any]], user_id: int) -> dict[str, Any]:
    return next(persona for persona in personas if persona["user_id"] == user_id)


def test_allergy_ingredients_never_appear(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    allergy_ids: Callable[[Iterable[str]], frozenset[int]],
    cfg: RankConfig,
) -> None:
    persona = _persona(personas, 1006)
    banned = allergy_ids(persona["allergy_group_codes"])
    assert any(recipe.all_ids & banned for recipe in pool), (
        "차단할 레시피가 풀에 있어야 검증이 됩니다"
    )

    selected = select_candidates(pool, context_for(persona), cfg)

    assert selected.candidates
    assert all(not (recipe.all_ids & banned) for recipe in selected.candidates)


def test_allergy_cut_survives_popularity_fallback(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    allergy_ids: Callable[[Iterable[str]], frozenset[int]],
    cfg: RankConfig,
) -> None:
    """후보가 없어 인기순으로 떨어져도 알레르기 재료는 감점이 아니라 제외입니다."""
    persona = {
        **_persona(personas, 1004),
        "pantry_ingredient_ids": [52],
        "allergy_group_codes": ["EGG"],
    }
    banned = allergy_ids(["EGG"])

    selected = select_candidates(pool, context_for(persona), cfg)

    assert selected.fallback_stage == FALLBACK_POPULARITY
    assert all(not (recipe.all_ids & banned) for recipe in selected.candidates)


def test_fallback_sets_degraded_and_still_fills_the_list(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    selected = select_candidates(pool, context_for(_persona(personas, 1004)), cfg)

    assert selected.degraded
    assert len(selected.candidates) >= cfg.min_candidates


def test_enough_candidates_do_not_degrade(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    selected = select_candidates(pool, context_for(_persona(personas, 1005)), cfg)

    assert not selected.degraded
    assert selected.fallback_stage == FALLBACK_NONE
    assert selected.max_missing == cfg.max_missing
    assert cfg.min_candidates <= len(selected.candidates) <= cfg.candidate_limit


def test_candidates_are_ordered_by_missing_then_popularity(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    ctx = context_for(_persona(personas, 1005))

    selected = select_candidates(pool, ctx, cfg)

    keys = [
        (len(recipe.essential_ids - ctx.pantry_ids), -(recipe.popularity_score or 0.0))
        for recipe in selected.candidates
    ]
    assert keys == sorted(keys)


def test_cook_minutes_cap_is_part_of_eligibility(
    pool: list[RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    ctx = make_context(pantry=range(1, 61), max_cook_minutes=20)

    found = retrieve(pool, ctx, pantry=ctx.pantry_ids, max_missing=2, limit=500)

    assert found
    assert all(recipe.cook_minutes is not None and recipe.cook_minutes <= 20 for recipe in found)


def test_relaxing_missing_count_is_the_first_fallback(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    """k=2 로는 0건, k=3 으로 25건이면 3 까지만 풀고 멈춥니다."""
    recipes = [make_recipe(i, essential=[1, 2, 3, 4]) for i in range(25)]

    selected = select_candidates(recipes, make_context(pantry=[1]), cfg)

    assert selected.fallback_stage == FALLBACK_RELAX_MISSING
    assert selected.max_missing == 3
    assert len(selected.candidates) == 25


def test_substitutes_are_only_used_after_relaxation_fails(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    recipes = [make_recipe(i, essential=[1, 2, 3, 4, 5, 6]) for i in range(25)]
    history = UserHistory(substitute_ids=frozenset({2}))

    without = select_candidates(recipes, make_context(pantry=[1]), cfg)
    with_substitute = select_candidates(recipes, make_context(pantry=[1], history=history), cfg)

    assert without.fallback_stage == FALLBACK_POPULARITY
    assert with_substitute.fallback_stage == FALLBACK_SUBSTITUTE
    assert len(with_substitute.candidates) == 25


def test_popularity_fallback_orders_by_popularity(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    recipes = [make_recipe(i, essential=[99], popularity_score=i / 10) for i in range(5)]

    selected = select_candidates(recipes, make_context(pantry=[1]), cfg)

    assert selected.fallback_stage == FALLBACK_POPULARITY
    assert [recipe.recipe_id for recipe in selected.candidates] == [4, 3, 2, 1, 0]


def test_missing_ids_are_sorted(make_recipe: Callable[..., RecipeCandidate]) -> None:
    assert missing_ids(make_recipe(1, essential=[9, 3, 5]), frozenset({3})) == (5, 9)


def test_recipe_without_essentials_is_eligible(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    recipe = make_recipe(1, essential=[], extra=[52])

    assert is_eligible(recipe, make_context(pantry=[1]), pantry=frozenset({1}), max_missing=2)


def test_recipe_sharing_no_pantry_item_is_not_eligible(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    """부족 수가 k 이하라도 보유 재료와 하나도 겹치지 않으면 후보가 아닙니다."""
    recipe = make_recipe(1, essential=[7, 8])

    assert not is_eligible(recipe, make_context(pantry=[1]), pantry=frozenset({1}), max_missing=2)


def test_popularity_fallback_keeps_the_cook_time_cap_when_it_can(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    """20분 요청에 90분 레시피가 가는 것은 마지막 수단입니다. 인기가 높아도 상한이 먼저입니다."""
    short = [make_recipe(i, essential=[99], cook_minutes=10) for i in range(25)]
    long = [
        make_recipe(100 + i, essential=[99], cook_minutes=90, popularity_score=0.9)
        for i in range(25)
    ]

    selected = select_candidates(short + long, make_context(pantry=[1], max_cook_minutes=20), cfg)

    assert selected.fallback_stage == FALLBACK_POPULARITY
    assert len(selected.candidates) == 25
    assert all(recipe.cook_minutes == 10 for recipe in selected.candidates)


def test_popularity_fallback_drops_the_cap_only_when_too_few(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    short = [make_recipe(i, essential=[99], cook_minutes=10) for i in range(5)]
    long = [make_recipe(100 + i, essential=[99], cook_minutes=90) for i in range(25)]

    selected = select_candidates(short + long, make_context(pantry=[1], max_cook_minutes=20), cfg)

    assert selected.fallback_stage == FALLBACK_POPULARITY
    assert len(selected.candidates) == 30
