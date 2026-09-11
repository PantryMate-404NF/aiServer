"""③ Re-ranking. 탐색 슬롯의 수와 자리, 노출확률의 의미, MMR 다양성."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

import pytest

from features.recommend.engine import rerank
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext, UserHistory
from features.recommend.engine.persona import cold_persona
from features.recommend.engine.score import score_all
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate, ScoredCandidate

CORPUS = CorpusStats(flavor_mean=(0.5,) * 6)
RUNS = 60


@pytest.fixture
def pool(
    make_recipe: Callable[..., RecipeFeature],
    make_candidate: Callable[..., Candidate],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> tuple[list[ScoredCandidate], dict[int, RecipeFeature]]:
    """점수가 내려가는 60건. 재료가 전부 달라 MMR 이 순서를 크게 흔들지 않습니다."""
    recipes = {i: make_recipe(i, essential=[i * 3, i * 3 + 1]) for i in range(60)}
    candidates = [make_candidate(i, coverage=1.0 - i / 100, cluster_id=i % 8) for i in range(60)]
    scored = score_all(candidates, recipes, make_context(pantry=range(200)), CORPUS, policy)
    return scored, recipes


def test_exploration_is_a_fifth_of_the_list(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    scored, recipes = pool

    items = rerank.rerank(scored, recipes, make_context(), CORPUS, policy, rng, top_k=20)

    assert len(items) == 20
    assert [item.final_rank for item in items] == list(range(1, 21))
    assert sum(item.is_exploration for item in items) == 4


def test_a_cold_user_gets_more_exploration(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """취향을 전혀 모르면 개인화할 재료가 없으므로 목록을 더 다양하게 냅니다."""
    scored, recipes = pool
    cold = make_context(persona=cold_persona())
    items = rerank.rerank(scored, recipes, cold, CORPUS, policy, rng, top_k=20)
    assert sum(item.is_exploration for item in items) == round(20 * policy.cold_exploration_ratio)
    assert rerank.exploration_ratio(cold, policy) == policy.cold_exploration_ratio
    assert rerank.exploration_ratio(make_context(), policy) == policy.exploration_ratio


def test_propensity_is_a_probability(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """A 트랙 계약은 확률입니다(0 < p <= 1). 역수를 실으면 RankedItem 검증에서 터집니다."""
    scored, recipes = pool

    items = rerank.rerank(scored, recipes, make_context(), CORPUS, policy, rng, top_k=20)

    for item in items:
        assert item.propensity is not None
        assert 0.0 < item.propensity <= 1.0
    assert all(item.propensity == 1.0 for item in items if not item.is_exploration)
    assert all(item.propensity < 1.0 for item in items if item.is_exploration)


def test_exploration_records_which_path_picked_it(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """두 경로를 구분하지 않으면 off-policy 분석에서 다시 나눌 수 없습니다."""
    scored, recipes = pool

    items = rerank.rerank(scored, recipes, make_context(), CORPUS, policy, rng, top_k=20)

    sources = {item.explore_source for item in items if item.is_exploration}
    assert sources <= {rerank.SOURCE_UNIFORM, rerank.SOURCE_THOMPSON}
    assert all(item.explore_source is None for item in items if not item.is_exploration)


def test_exploration_positions_vary_across_runs(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """자리를 고정하면 위치별 검사확률 곡선을 구할 수 없습니다."""
    scored, recipes = pool
    layouts: set[tuple[int, ...]] = set()
    seen: list[int] = []
    for _ in range(RUNS):
        items = rerank.rerank(scored, recipes, make_context(), CORPUS, policy, rng, top_k=20)
        positions = tuple(item.final_rank for item in items if item.is_exploration)
        layouts.add(positions)
        seen.extend(positions)

    assert len(layouts) > 1
    assert min(seen) <= 5
    assert max(seen) >= 16


def test_personal_slots_are_deterministic(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """탐색 자리만 무작위입니다. 점수와 개인화 선택에 난수가 섞이면 재현이 안 됩니다."""
    scored, recipes = pool
    ctx = make_context()

    runs = [
        [
            item.recipe_id
            for item in rerank.rerank(scored, recipes, ctx, CORPUS, policy, rng, top_k=20)
            if not item.is_exploration
        ]
        for _ in range(3)
    ]

    assert runs[0] == runs[1] == runs[2]


def test_short_candidate_list_returns_everything(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    scored, recipes = pool

    items = rerank.rerank(scored[:5], recipes, make_context(), CORPUS, policy, rng, top_k=20)

    assert len(items) == 5
    assert rerank.rerank([], recipes, make_context(), CORPUS, policy, rng, top_k=20) == []


def test_mmr_prefers_a_dissimilar_second_pick(
    make_recipe: Callable[..., RecipeFeature],
    make_candidate: Callable[..., Candidate],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> None:
    """점수만 보면 1, 2 순서지만 2 는 1 과 재료가 같아 3 이 먼저 옵니다."""
    recipes = {
        1: make_recipe(1, essential=[1, 2, 3]),
        2: make_recipe(2, essential=[1, 2, 3]),
        3: make_recipe(3, essential=[7, 8, 9]),
    }
    candidates = [
        make_candidate(1, coverage=1.0),
        make_candidate(2, coverage=0.95),
        make_candidate(3, coverage=0.8),
    ]
    scored = score_all(candidates, recipes, make_context(pantry=range(20)), CORPUS, policy)

    picked = rerank.mmr_select(scored, recipes, 3, {}, policy.mmr_lambda)

    assert [item.recipe_id for item, _ in picked] == [1, 3, 2]
    assert picked[1][1] == pytest.approx(0.0)
    assert picked[2][1] > 0.0


def test_reason_is_filled_from_the_data_track_templates(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """사유는 A 트랙 reason.py 가 만듭니다. 빈 문구나 자리표시자가 남으면 안 됩니다."""
    scored, recipes = pool

    items = rerank.rerank(scored, recipes, make_context(), CORPUS, policy, rng, top_k=20)

    for item in items:
        assert item.reason
        assert "{" not in item.reason
        assert "None" not in item.reason


def test_reason_context_skips_values_it_does_not_know(
    make_recipe: Callable[..., RecipeFeature],
    make_context: Callable[..., UserContext],
    make_candidate: Callable[..., Candidate],
    policy: RankingPolicy,
) -> None:
    """모르는 값을 지어내지 않고 빼면 그 피처가 사유 후보에서 자동으로 빠집니다."""
    scored = score_all(
        [make_candidate(1)],
        {1: make_recipe(1, essential=[1], cuisine=None)},
        make_context(pantry=[1]),
        CORPUS,
        policy,
    )[0]

    values = rerank.reason_context(
        scored, make_recipe(1, essential=[1], cuisine=None), make_context(pantry=[1]), CORPUS
    )

    assert "cuisine" not in values
    assert "expiring_name" not in values


def test_thompson_follows_a_strong_prior(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """반응이 몰린 클러스터가 Thompson 슬롯을 자주 가져가야 합니다."""
    scored, _ = pool
    ctx = make_context(
        history=UserHistory(
            cluster_seen={c: 20 for c in range(8)},
            cluster_hits={0: 19, **{c: 0 for c in range(1, 8)}},
        )
    )
    by_id = {item.recipe_id: item for item in scored}

    hits = 0
    for _ in range(RUNS):
        picked = rerank.pick_exploration(scored, ctx, policy, rng, 2)
        chosen: Sequence[int] = [
            item.recipe_id for item, _, source in picked if source == "thompson"
        ]
        hits += sum(1 for rid in chosen if by_id[rid].cluster_id == 0)

    assert hits >= RUNS * 0.5
