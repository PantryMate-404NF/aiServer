"""파이프라인 조립. 같은 입력이면 같은 점수, 응답 계약의 모양, 서빙 로그의 재료."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

import pytest

from features.recommend import service
from features.recommend.schema import (
    CorpusStats,
    RankConfig,
    RecipeCandidate,
    RecommendRequest,
    UserContext,
)


def _persona(personas: list[dict[str, Any]], user_id: int) -> dict[str, Any]:
    return next(persona for persona in personas if persona["user_id"] == user_id)


def test_deterministic_scores_for_the_same_input(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    """탐색 슬롯의 자리만 무작위입니다. 점수 자체에 난수가 섞이면 이 테스트가 먼저 깨집니다."""
    ctx = context_for(_persona(personas, 1005))

    first = service.run_pipeline(ctx, pool, corpus, cfg, rng)
    second = service.run_pipeline(ctx, pool, corpus, cfg, rng)

    scores_first = {item.candidate.recipe_id: item.score for item in first.log.candidates}
    scores_second = {item.candidate.recipe_id: item.score for item in second.log.candidates}
    assert scores_first == scores_second
    personal_first = [
        (item.recipe_id, item.match_score)
        for item in first.response.recommendations
        if not item.is_exploration
    ]
    personal_second = [
        (item.recipe_id, item.match_score)
        for item in second.response.recommendations
        if not item.is_exploration
    ]
    assert personal_first == personal_second


def test_response_matches_the_contract(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    result = service.run_pipeline(context_for(_persona(personas, 1005)), pool, corpus, cfg, rng)
    response = result.response

    assert isinstance(response.request_id, UUID)
    assert len(response.recommendations) == 20
    assert [item.rank for item in response.recommendations] == list(range(1, 21))
    assert len({item.recipe_id for item in response.recommendations}) == 20
    assert sum(item.is_exploration for item in response.recommendations) == 4
    for item in response.recommendations:
        assert 0.0 <= item.match_score <= 1.0
        assert item.missing_count == len(item.missing_ingredient_ids)
        assert item.reason
    assert not response.meta.degraded
    assert response.meta.fallback_stage == "none"
    assert response.meta.candidate_count == len(result.log.candidates)
    assert response.meta.config_fingerprint == cfg.fingerprint()


def test_degraded_meta_for_a_tiny_pantry(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """후보가 모자라도 200 이며 meta.degraded 와 경고 로그로만 알립니다."""
    with caplog.at_level(logging.WARNING, logger="features.recommend.service"):
        result = service.run_pipeline(context_for(_persona(personas, 1004)), pool, corpus, cfg, rng)

    assert result.response.meta.degraded
    assert len(result.response.recommendations) == 20
    assert "degraded" in caplog.text
    assert "user_id=1004" in caplog.text


def test_every_persona_is_served_its_top_k(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    for persona in personas:
        request = RecommendRequest.model_validate(persona)
        result = service.run_pipeline(context_for(persona), pool, corpus, cfg, rng)

        assert len(result.response.recommendations) == request.top_k, persona["user_id"]


def test_serving_log_matches_the_response(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    ctx = context_for(_persona(personas, 1002))

    result = service.run_pipeline(ctx, pool, corpus, cfg, rng)

    served_ids = tuple(item.recipe_id for item in result.response.recommendations)
    assert result.log.served_recipe_ids == served_ids
    assert set(result.log.propensity_scores) == set(served_ids)
    assert result.log.request_id == result.response.request_id
    assert result.log.config_fingerprint == result.response.meta.config_fingerprint
    assert result.log.pantry_snapshot == tuple(sorted(ctx.pantry_ids))
    assert result.log.user_id == 1002


def test_allergy_hard_cut_end_to_end(
    pool: list[RecipeCandidate],
    personas: list[dict[str, Any]],
    context_for: Callable[..., UserContext],
    allergy_ids: Callable[[Iterable[str]], frozenset[int]],
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    persona = _persona(personas, 1006)
    banned = allergy_ids(persona["allergy_group_codes"])
    by_id = {recipe.recipe_id: recipe for recipe in pool}

    result = service.run_pipeline(context_for(persona), pool, corpus, cfg, rng)

    assert all(
        not (by_id[item.recipe_id].all_ids & banned) for item in result.response.recommendations
    )
