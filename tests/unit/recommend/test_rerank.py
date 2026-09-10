"""Stage 3. 탐색 슬롯 비율, 위치 무작위화, MMR 다양성."""

from __future__ import annotations

import random
from collections.abc import Callable

import pytest

from features.recommend.engine.rerank import jaccard_idf, mmr_select, pick_exploration, rerank
from features.recommend.schema import (
    CorpusStats,
    RankConfig,
    RecipeCandidate,
    ScoredCandidate,
    UserContext,
    UserHistory,
)

CUISINES = ("한식", "양식", "중식", "일식")
RUNS = 100


def _scored(recipe: RecipeCandidate, score: float) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=recipe, missing_ids=(), blocks={}, base_score=score, score=score
    )


@pytest.fixture
def scored_pool(make_recipe: Callable[..., RecipeCandidate]) -> list[ScoredCandidate]:
    """점수가 내려가는 60건. 요리군은 네 가지가 돌아가며 붙고 재료는 전부 다릅니다."""
    return [
        _scored(
            make_recipe(
                i,
                essential=[i * 3, i * 3 + 1],
                cuisine=CUISINES[i % len(CUISINES)],
                quality_score=(i % 7) / 7,
            ),
            1.0 - i / 100,
        )
        for i in range(60)
    ]


@pytest.fixture
def ctx(make_context: Callable[..., UserContext]) -> UserContext:
    return make_context(preferred_cuisines=frozenset({"한식"}), top_k=20)


def test_exploration_count_is_twenty_percent(
    scored_pool: list[ScoredCandidate], ctx: UserContext, cfg: RankConfig, rng: random.Random
) -> None:
    served = rerank(scored_pool, ctx, CorpusStats(), cfg, rng)

    assert len(served) == 20
    assert sum(item.is_exploration for item in served) == 4


def test_exploration_positions_vary_across_runs(
    scored_pool: list[ScoredCandidate], ctx: UserContext, cfg: RankConfig, rng: random.Random
) -> None:
    """탐색 슬롯이 늘 같은 자리면 사용자가 건너뜁니다. 100회 돌려 자리를 봅니다."""
    layouts: set[tuple[int, ...]] = set()
    positions: list[int] = []
    for _ in range(RUNS):
        served = rerank(scored_pool, ctx, CorpusStats(), cfg, rng)
        explored = tuple(item.rank for item in served if item.is_exploration)
        layouts.add(explored)
        positions.extend(explored)

    assert len(layouts) > 1
    assert 5.0 < sum(positions) / len(positions) < 16.0


def test_ranks_are_contiguous_and_recipes_unique(
    scored_pool: list[ScoredCandidate], ctx: UserContext, cfg: RankConfig, rng: random.Random
) -> None:
    served = rerank(scored_pool, ctx, CorpusStats(), cfg, rng)

    assert [item.rank for item in served] == list(range(1, 21))
    assert len({item.scored.candidate.recipe_id for item in served}) == 20


def test_exploration_comes_from_non_preferred_cuisines(
    scored_pool: list[ScoredCandidate], ctx: UserContext, cfg: RankConfig, rng: random.Random
) -> None:
    served = rerank(scored_pool, ctx, CorpusStats(), cfg, rng)

    assert all(item.scored.candidate.cuisine != "한식" for item in served if item.is_exploration)


def test_propensity_is_one_for_personal_and_above_one_for_exploration(
    scored_pool: list[ScoredCandidate], ctx: UserContext, cfg: RankConfig, rng: random.Random
) -> None:
    served = rerank(scored_pool, ctx, CorpusStats(), cfg, rng)

    assert all(item.propensity == 1.0 for item in served if not item.is_exploration)
    assert all(item.propensity >= 1.0 for item in served if item.is_exploration)


def test_no_novel_cuisine_falls_back_to_personal_only(
    scored_pool: list[ScoredCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    ctx = make_context(preferred_cuisines=frozenset(CUISINES), top_k=20)

    served = rerank(scored_pool, ctx, CorpusStats(), cfg, rng)

    assert len(served) == 20
    assert not any(item.is_exploration for item in served)


def test_short_candidate_list_returns_everything(
    scored_pool: list[ScoredCandidate], ctx: UserContext, cfg: RankConfig, rng: random.Random
) -> None:
    served = rerank(scored_pool[:5], ctx, CorpusStats(), cfg, rng)

    assert len(served) == 5
    assert rerank([], ctx, CorpusStats(), cfg, rng) == ()


def test_mmr_prefers_a_dissimilar_second_pick(make_recipe: Callable[..., RecipeCandidate]) -> None:
    """점수만 보면 A, B 순서지만 B 는 A 와 재료가 같아 C 가 먼저 옵니다."""
    a = _scored(make_recipe(1, essential=[1, 2, 3]), 1.0)
    b = _scored(make_recipe(2, essential=[1, 2, 3]), 0.95)
    c = _scored(make_recipe(3, essential=[7, 8, 9]), 0.8)

    picked = mmr_select([a, b, c], 3, {}, 0.7)

    assert [item.candidate.recipe_id for item in picked] == [1, 3, 2]


def test_jaccard_idf_discounts_common_ingredients() -> None:
    left = frozenset({1, 9})
    right = frozenset({1})

    assert jaccard_idf(left, right, {}) == pytest.approx(0.5)
    assert jaccard_idf(left, right, {1: 0.1, 9: 3.0}) == pytest.approx(0.1 / 3.1)
    assert jaccard_idf(frozenset(), frozenset(), {}) == 0.0


def test_thompson_follows_a_strong_prior(
    scored_pool: list[ScoredCandidate],
    make_context: Callable[..., UserContext],
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    """중식이 거의 항상 이기는 사후 분포면 Thompson 슬롯(첫 번째 픽)은 거의 항상 중식입니다."""
    priors = {"중식": (50.0, 1.0), "양식": (1.0, 50.0), "일식": (1.0, 50.0)}
    ctx = make_context(
        preferred_cuisines=frozenset({"한식"}), history=UserHistory(cuisine_priors=priors)
    )

    wins = sum(
        pick_exploration(scored_pool, ctx, cfg, rng, 2)[0][0].candidate.cuisine == "중식"
        for _ in range(RUNS)
    )

    assert wins >= RUNS * 0.9
