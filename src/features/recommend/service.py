"""추천 파이프라인의 흐름 조립. 단계 구현은 engine/ 에 있고 여기는 순서만 있습니다."""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID, uuid4

from features.recommend.engine import candidate, explain, penalty, rank, rerank
from features.recommend.schema import RecommendedItem, RecommendMeta, RecommendResponse
from features.recommend.stage import (
    CorpusStats,
    RankConfig,
    RecipeCandidate,
    ScoredCandidate,
    ServedItem,
    UserContext,
)

logger = logging.getLogger(__name__)

MILLISECONDS_PER_SECOND = 1000


@dataclass(frozen=True)
class ServingLog:
    """recommendation_log 한 행의 재료. repository 가 JSONB 로 바꿔 적재합니다."""

    request_id: UUID
    user_id: int
    config_fingerprint: str
    pantry_snapshot: tuple[int, ...]
    served: tuple[ServedItem, ...]
    candidates: tuple[ScoredCandidate, ...]
    latency_ms: int

    @property
    def served_recipe_ids(self) -> tuple[int, ...]:
        return tuple(item.scored.candidate.recipe_id for item in self.served)

    @property
    def propensity_scores(self) -> dict[int, float]:
        return {item.scored.candidate.recipe_id: item.propensity for item in self.served}


@dataclass(frozen=True)
class PipelineResult:
    response: RecommendResponse
    log: ServingLog


def run_pipeline(
    ctx: UserContext,
    pool: Sequence[RecipeCandidate],
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
) -> PipelineResult:
    """Stage 1~3 을 순서대로 돌립니다. I/O 가 없어 같은 입력이면 점수가 같습니다."""
    started = perf_counter()
    selected = candidate.select_candidates(pool, ctx, cfg)
    scored = tuple(
        penalty.apply_penalties(rank.score_candidate(recipe, ctx, corpus, cfg), ctx, cfg)
        for recipe in selected.candidates
    )
    served = rerank.rerank(scored, ctx, corpus, cfg, rng)
    stats = explain.block_stats(scored)
    items = [_to_item(entry, ctx, corpus, stats, cfg) for entry in served]
    latency_ms = int((perf_counter() - started) * MILLISECONDS_PER_SECOND)

    if selected.degraded:
        logger.warning(
            "recommend degraded user_id=%s stage=%s candidates=%d",
            ctx.user_id,
            selected.fallback_stage,
            len(scored),
        )

    request_id = uuid4()
    fingerprint = cfg.fingerprint()
    response = RecommendResponse(
        request_id=request_id,
        recommendations=items,
        meta=RecommendMeta(
            degraded=selected.degraded,
            fallback_stage=selected.fallback_stage,
            candidate_count=len(scored),
            latency_ms=latency_ms,
            config_fingerprint=fingerprint,
        ),
    )
    log = ServingLog(
        request_id=request_id,
        user_id=ctx.user_id,
        config_fingerprint=fingerprint,
        pantry_snapshot=tuple(sorted(ctx.pantry_ids)),
        served=served,
        candidates=scored,
        latency_ms=latency_ms,
    )
    return PipelineResult(response, log)


def _to_item(
    entry: ServedItem,
    ctx: UserContext,
    corpus: CorpusStats,
    stats: dict[str, explain.BlockStats],
    cfg: RankConfig,
) -> RecommendedItem:
    scored = entry.scored
    recipe = scored.candidate
    return RecommendedItem(
        rank=entry.rank,
        recipe_id=recipe.recipe_id,
        recipe_title=recipe.title,
        match_score=scored.score,
        cook_minutes=recipe.cook_minutes,
        missing_ingredient_ids=list(scored.missing_ids),
        missing_count=len(scored.missing_ids),
        reason=explain.explain(
            scored, ctx, corpus, stats, cfg, is_exploration=entry.is_exploration
        ),
        matched_product_ids=list(recipe.product_ids),
        is_exploration=entry.is_exploration,
    )
