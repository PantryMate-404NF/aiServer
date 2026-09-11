"""②③ 조립. 계약 준수, 로그에 남는 값, 12인 프로필 전수 통과."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from typing import Any

import pytest

from features.recommend import service
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.engine.rank import check_trace_params, keep_candidates, merge_served_detail
from features.recommend.enums import CANDIDATE_KEEP, FEATURE_KEYS, Stage
from features.recommend.policy import POLICY_ID, RankingPolicy
from features.recommend.stage import Candidate


def _profile(profiles: list[dict[str, Any]], user_id: int) -> dict[str, Any]:
    return next(row for row in profiles if row["user_id"] == user_id)


def test_pipeline_meets_the_stage_contract(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    profile = _profile(personas, 1005)
    ctx = context_for(profile)
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(
        candidates, recipes, ctx, corpus, policy, rng, top_k=20, rng_seed=7
    )

    assert len(result.items) == 20
    assert [item.final_rank for item in result.items] == list(range(1, 21))
    assert len({item.recipe_id for item in result.items}) == 20
    for item in result.items:
        assert set(item.features) == set(FEATURE_KEYS)
        assert 0.0 <= item.score <= 1.0
        assert item.propensity is not None and 0.0 < item.propensity <= 1.0


def test_trace_params_carry_every_frozen_key(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """값이 아니라 정의가 소급 불가입니다. 키가 없으면 propensity 를 재구성할 수 없습니다."""
    ctx = context_for(_profile(personas, 1002))
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng, rng_seed=3)

    for stage in result.stages:
        assert check_trace_params(stage.params) == []
        assert stage.params["policy_id"] == POLICY_ID
        assert stage.params["rng_seed"] == 3
    assert [stage.name for stage in result.stages] == [Stage.RANKING, Stage.RERANK]


def test_score_stats_show_the_spread(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """랭커가 조용히 납작해지는 것을 이 값으로 알아챕니다."""
    ctx = context_for(_profile(personas, 1005))
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)

    stats = result.stages[0].score_stats
    assert stats["min"] <= stats["p50"] <= stats["max"]
    assert stats["max"] > stats["min"]


def test_served_items_survive_the_candidate_cut(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """탐색 아이템은 상위 50 밖으로 떨어질 수 있고, 그것이 propensity != 1 인 유일한 행입니다."""
    ctx = context_for(_profile(personas, 1005))
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)
    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)
    served = [item.recipe_id for item in result.items]

    merged = merge_served_detail(result.scored, result.items)
    kept = keep_candidates(merged, served)

    assert set(served) <= {row.recipe_id for row in kept}
    assert len(kept) >= min(CANDIDATE_KEEP["real"], len(result.scored))
    explored = {item.recipe_id for item in result.items if item.is_exploration}
    logged = {row.recipe_id for row in kept if getattr(row, "propensity", 1.0) not in (None, 1.0)}
    assert explored <= logged


def test_every_profile_is_served(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """후보가 모자란 프로필은 완화 계획을 따라 다시 조회하면 채워져야 합니다."""
    for profile in personas:
        ctx = context_for(profile)
        top_k = int(profile.get("top_k", 20))
        candidates = _retrieve_with_fallback(retrieve, ctx, policy, top_k)

        result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng, top_k=top_k)

        assert len(result.items) == min(top_k, len(candidates)), profile["user_id"]
        assert len(result.items) >= 10, profile["user_id"]


def test_allergy_cut_is_the_retrieval_stage_not_the_ranking(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    allergy_ids: Callable[[Iterable[str]], frozenset[int]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """제외는 ① 에서만 합니다. ② 는 걸러진 뒤의 후보만 봅니다."""
    profile = _profile(personas, 1006)
    banned = allergy_ids(profile["allergy_group_codes"])
    ctx = context_for(profile)
    candidates = retrieve(ctx.pantry_ids, allergy=banned, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)

    assert result.items
    assert all(not (recipes[item.recipe_id].all_ids & banned) for item in result.items)


def test_counters_are_isolated_from_ranking() -> None:
    """로그 실패를 삼키되 반드시 셉니다. 카운터는 프로세스 메모리입니다."""
    service.reset_counters()
    service.bump("log_write_failed")
    service.bump("log_write_failed", 2)

    assert service.counters()["log_write_failed"] == 3

    service.reset_counters()
    assert service.counters() == {}


def _retrieve_with_fallback(
    retrieve: Callable[..., list[Candidate]],
    ctx: UserContext,
    policy: RankingPolicy,
    top_k: int,
) -> list[Candidate]:
    """`engine/candidate.py` 의 계획대로 다시 조회합니다. 운영에서는 repository 가 합니다."""
    from features.recommend.engine import candidate as plan_module

    plan = plan_module.first_plan(policy)
    rows = retrieve(ctx.pantry_ids, max_missing=plan.max_missing, max_minutes=ctx.max_cook_minutes)
    while True:
        nxt = plan_module.next_plan(plan, len(rows), policy, top_k)
        if nxt is None:
            return plan_module.dedupe(rows)
        plan = nxt
        wider = retrieve(
            ctx.pantry_ids,
            max_missing=plan.max_missing,
            max_minutes=ctx.max_cook_minutes,
            ignore_missing=plan.stage == plan_module.FALLBACK_POPULARITY,
        )
        rows = plan_module.dedupe([*rows, *wider])


def test_fingerprint_changes_with_the_policy(policy: RankingPolicy) -> None:
    """지문이 같으면 그때의 계산을 되살릴 수 있어야 합니다. 값 하나만 달라도 달라집니다."""
    assert policy.fingerprint() == RankingPolicy().fingerprint()
    assert policy.fingerprint() != RankingPolicy(mmr_lambda=0.8).fingerprint()
    assert policy.fingerprint() != policy.fingerprint({"f_coverage": 0.9})


def test_policy_knobs_stay_within_their_meaning(policy: RankingPolicy) -> None:
    assert 0.0 < policy.uniform_share <= 1.0
    assert 0.0 < policy.exploration_ratio < 1.0
    assert policy.max_missing < policy.max_missing_relaxed
    assert policy.mmr_pool_size <= policy.candidate_limit
    with pytest.raises(KeyError):
        _ = policy.trace_params(top_k=20, n_explore=4, rng_seed=0, max_missing_final=2)["없는키"]
