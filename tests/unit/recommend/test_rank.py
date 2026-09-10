"""Stage 2. Zero-Drop 정규화와 맛 벡터 중심화."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest

from features.recommend.engine.rank import (
    BLOCKS,
    context_score,
    cuisine_fit,
    match_score,
    quality_score,
    score_candidate,
    taste_score,
    time_fit,
    weighted_sum,
)
from features.recommend.schema import CorpusStats, RankConfig, RecipeCandidate, UserContext

CENTER = (0.5, 0.5, 0.5)
CENTERED = CorpusStats(flavor_mean=CENTER)


def test_zero_drop_excludes_expiring_block_when_nothing_is_expiring(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    """임박 재료가 없으면 w_expiring 이 분모에서도 빠져 다른 블록의 비중이 그대로 유지됩니다."""
    recipe = make_recipe(
        1, essential=[1, 2], flavor_vec=(1.0, 0.5, 0.5), popularity_score=1.0, quality_score=1.0
    )
    ctx = make_context(pantry=[1], taste=(1.0, 0.5, 0.5), max_cook_minutes=30)

    scored = score_candidate(recipe, ctx, CENTERED, cfg)

    assert scored.blocks["expiring"] is None
    assert scored.blocks["match"] == pytest.approx(0.5)
    measured = cfg.w_match * 0.5 + cfg.w_taste + cfg.w_quality + cfg.w_ctx
    assert scored.base_score == pytest.approx(measured / (1.0 - cfg.w_expiring))
    assert scored.base_score != pytest.approx(measured)


def test_all_blocks_measured_use_the_full_weight_sum(
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
) -> None:
    recipe = make_recipe(
        1, essential=[1, 2], flavor_vec=(1.0, 0.5, 0.5), popularity_score=1.0, quality_score=1.0
    )
    ctx = make_context(pantry=[1], expiring=[2], taste=(1.0, 0.5, 0.5), max_cook_minutes=30)

    scored = score_candidate(recipe, ctx, CENTERED, cfg)

    assert all(scored.blocks[name] is not None for name in BLOCKS)
    assert scored.base_score == pytest.approx(cfg.w_match * 0.5 + 0.15 + 0.31 + 0.15 + 0.10)


def test_centering_is_required() -> None:
    """코퍼스 평균이 없으면 계산하지 않습니다. 중심화 없는 코사인은 전부 비슷하다고 답합니다."""
    assert taste_score((0.9, 0.1, 0.1), (0.9, 0.1, 0.1), None) is None


def test_centering_moves_similarity_around_the_mean() -> None:
    user = (0.9, 0.5, 0.5)

    assert taste_score(user, (0.8, 0.5, 0.5), CENTER) == pytest.approx(1.0)
    assert taste_score(user, (0.2, 0.5, 0.5), CENTER) == pytest.approx(0.0)


def test_centering_removes_the_positive_vector_bias() -> None:
    """정반대 취향도 원점 기준 코사인은 비슷하다고(0.5 초과) 말합니다."""
    user = (0.9, 0.1, 0.1)
    recipe = (0.1, 0.9, 0.9)

    uncentered = taste_score(user, recipe, (0.0, 0.0, 0.0))
    centered = taste_score(user, recipe, CENTER)

    assert uncentered is not None and uncentered > 0.5
    assert centered == pytest.approx(0.0)


def test_user_at_corpus_mean_has_no_taste_signal() -> None:
    assert taste_score(CENTER, (0.9, 0.1, 0.1), CENTER) is None


def test_match_score_counts_missing_essentials(make_recipe: Callable[..., RecipeCandidate]) -> None:
    assert match_score(make_recipe(1, essential=[1, 2, 3, 4]), frozenset({1, 2})) == pytest.approx(
        0.5
    )
    assert match_score(make_recipe(2, essential=[]), frozenset()) == pytest.approx(1.0)


def test_time_fit_penalises_overrun(make_recipe: Callable[..., RecipeCandidate]) -> None:
    assert time_fit(make_recipe(1, cook_minutes=45), 30) == pytest.approx(0.5)
    assert time_fit(make_recipe(1, cook_minutes=20), 30) == pytest.approx(1.0)
    assert time_fit(make_recipe(1, cook_minutes=20), None) is None
    assert time_fit(make_recipe(1, cook_minutes=None), 30) is None


def test_context_block_averages_time_and_cuisine_fit(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    """선호 요리군이 있으면 시간 적합과 반씩 섭니다. 없으면 시간 적합만 봅니다."""
    recipe = make_recipe(1, cook_minutes=30, cuisine="한식")
    liked = make_context(max_cook_minutes=30, preferred_cuisines=frozenset({"한식"}))
    disliked = make_context(max_cook_minutes=30, preferred_cuisines=frozenset({"양식"}))
    no_preference = make_context(max_cook_minutes=30)

    assert context_score(recipe, liked) == pytest.approx(1.0)
    assert context_score(recipe, disliked) == pytest.approx(0.5)
    assert context_score(recipe, no_preference) == pytest.approx(1.0)
    assert context_score(recipe, make_context()) is None


def test_cuisine_fit_is_unmeasurable_without_preference_or_cuisine(
    make_recipe: Callable[..., RecipeCandidate],
) -> None:
    assert cuisine_fit(make_recipe(1, cuisine=None), frozenset({"한식"})) is None
    assert cuisine_fit(make_recipe(1, cuisine="한식"), frozenset()) is None
    assert cuisine_fit(make_recipe(1, cuisine="한식"), frozenset({"양식"})) == 0.0
    assert cuisine_fit(make_recipe(1, cuisine="한식"), frozenset({"한식", "양식"})) == 1.0


def test_quality_uses_whatever_part_is_available(
    make_recipe: Callable[..., RecipeCandidate], cfg: RankConfig
) -> None:
    both = make_recipe(1, popularity_score=1.0, quality_score=0.0)
    only_quality = make_recipe(2, popularity_score=None, quality_score=0.8)
    neither = make_recipe(3, popularity_score=None, quality_score=None)

    assert quality_score(both, cfg) == pytest.approx(cfg.quality_popularity_share)
    assert quality_score(only_quality, cfg) == pytest.approx(0.8)
    assert quality_score(neither, cfg) is None


def test_no_measurable_block_gives_zero(cfg: RankConfig) -> None:
    assert weighted_sum(dict.fromkeys(BLOCKS), cfg) == 0.0


def test_scores_stay_within_unit_interval(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    corpus: CorpusStats,
    cfg: RankConfig,
) -> None:
    for persona in personas:
        ctx = context_for(persona)
        for recipe in pool:
            scored = score_candidate(recipe, ctx, corpus, cfg)
            assert 0.0 <= scored.base_score <= 1.0
            assert all(value is None or 0.0 <= value <= 1.0 for value in scored.blocks.values())


def test_near_neutral_user_taste_is_damped_toward_half() -> None:
    """전부 '보통' 인 사용자의 평균 대비 잡음이 방향으로 전폭 반영되면 안 됩니다."""
    mean = (0.47, 0.46, 0.58)
    user = (0.5, 0.5, 0.5)
    recipe = (0.9, 0.1, 0.1)

    full = taste_score(user, recipe, mean)
    damped = taste_score(user, recipe, mean, min_norm=0.25)

    norm = math.sqrt(sum((u - m) ** 2 for u, m in zip(user, mean, strict=True)))
    assert full is not None and damped is not None
    assert damped - 0.5 == pytest.approx((full - 0.5) * norm / 0.25)
    assert abs(damped - 0.5) < abs(full - 0.5)


def test_one_step_preference_keeps_full_taste_strength() -> None:
    """온보딩 한 단계(0.25) 차이면 신뢰 100% 입니다."""
    user = (0.75, 0.5, 0.5)
    recipe = (0.9, 0.5, 0.5)

    assert taste_score(user, recipe, CENTER, min_norm=0.25) == taste_score(user, recipe, CENTER)
