"""Stage 2. 5블록 Zero-Drop 가중합. 외부 I/O 가 없는 순수 함수만 둡니다."""

from __future__ import annotations

import math
from collections.abc import Mapping

from features.recommend.engine.candidate import missing_ids
from features.recommend.schema import (
    CorpusStats,
    FlavorVector,
    RankConfig,
    RecipeCandidate,
    ScoredCandidate,
    UserContext,
)

# 0 으로 나누는 것을 막는 값입니다. 점수에 보이는 영향은 없습니다.
EPSILON = 1e-9

BLOCK_MATCH = "match"
BLOCK_EXPIRING = "expiring"
BLOCK_TASTE = "taste"
BLOCK_QUALITY = "quality"
BLOCK_CTX = "ctx"
BLOCKS = (BLOCK_MATCH, BLOCK_EXPIRING, BLOCK_TASTE, BLOCK_QUALITY, BLOCK_CTX)


def score_candidate(
    recipe: RecipeCandidate, ctx: UserContext, corpus: CorpusStats, cfg: RankConfig
) -> ScoredCandidate:
    """블록 점수를 구해 가중합합니다. 감점은 penalty 가 이어서 적용합니다."""
    blocks = block_scores(recipe, ctx, corpus, cfg)
    base = weighted_sum(blocks, cfg)
    return ScoredCandidate(
        candidate=recipe,
        missing_ids=missing_ids(recipe, ctx.pantry_ids),
        blocks=blocks,
        base_score=base,
        score=base,
    )


def block_scores(
    recipe: RecipeCandidate, ctx: UserContext, corpus: CorpusStats, cfg: RankConfig
) -> dict[str, float | None]:
    return {
        BLOCK_MATCH: match_score(recipe, ctx.pantry_ids),
        BLOCK_EXPIRING: expiring_score(recipe, ctx.expiring_ids),
        BLOCK_TASTE: taste_score(
            ctx.taste_vec, recipe.flavor_vec, corpus.flavor_mean, cfg.taste_min_norm
        ),
        BLOCK_QUALITY: quality_score(recipe, cfg),
        BLOCK_CTX: context_score(recipe, ctx.max_cook_minutes),
    }


def weighted_sum(blocks: Mapping[str, float | None], cfg: RankConfig) -> float:
    """측정 불가(None) 블록은 분자와 분모에서 함께 뺍니다. 0점으로 두면 결측이 감점이 됩니다."""
    weights = cfg.weights()
    numerator = 0.0
    denominator = 0.0
    for name, value in blocks.items():
        if value is None:
            continue
        numerator += weights[name] * value
        denominator += weights[name]
    if denominator <= 0.0:
        return 0.0
    return _clamp(numerator / denominator)


def match_score(recipe: RecipeCandidate, pantry: frozenset[int]) -> float:
    """필수 재료 충족도. 필수 재료가 없는 레시피는 전부 갖춘 것으로 봅니다."""
    missing = len(recipe.essential_ids - pantry)
    return _clamp(1.0 - missing / (len(recipe.essential_ids) + EPSILON))


def expiring_score(recipe: RecipeCandidate, expiring: frozenset[int]) -> float | None:
    """임박 재료 소진율. 임박 재료가 없으면 측정 불가입니다."""
    if not expiring:
        return None
    return _clamp(len(recipe.essential_ids & expiring) / (len(expiring) + EPSILON))


def taste_score(
    user_vec: FlavorVector,
    recipe_vec: FlavorVector,
    corpus_mean: FlavorVector | None,
    min_norm: float = 0.0,
) -> float | None:
    """코퍼스 평균을 양쪽에서 뺀 뒤의 코사인 유사도를 0~1 로 옮깁니다.

    빼지 않으면 모든 벡터가 양수라 무엇을 넣어도 0.77 근처로 몰립니다. 평균이 없으면
    계산하지 않고 측정 불가로 둡니다. 어느 한쪽이 평균과 같으면 방향이 없어 역시 측정 불가입니다.

    코사인은 크기를 버리므로 평균에서 0.03 떨어진 사용자도 방향만으로 전폭 반영됩니다.
    사용자 벡터의 거리가 min_norm 에 못 미치면 그 비율만큼 0.5 쪽으로 눌러 잡음을 줄입니다.
    """
    if corpus_mean is None:
        return None
    user = [a - m for a, m in zip(user_vec, corpus_mean, strict=True)]
    recipe = [b - m for b, m in zip(recipe_vec, corpus_mean, strict=True)]
    user_norm = math.sqrt(sum(x * x for x in user))
    recipe_norm = math.sqrt(sum(x * x for x in recipe))
    if user_norm < EPSILON or recipe_norm < EPSILON:
        return None
    cosine = sum(a * b for a, b in zip(user, recipe, strict=True)) / (user_norm * recipe_norm)
    similarity = (cosine + 1.0) / 2.0
    confidence = 1.0 if min_norm <= 0.0 else min(1.0, user_norm / min_norm)
    return _clamp(0.5 + (similarity - 0.5) * confidence)


def quality_score(recipe: RecipeCandidate, cfg: RankConfig) -> float | None:
    """인기도와 품질의 가중 평균. 한쪽이 없으면 있는 쪽만, 둘 다 없으면 측정 불가입니다."""
    popularity = recipe.popularity_score
    quality = recipe.quality_score
    if popularity is None and quality is None:
        return None
    if popularity is None:
        return _clamp(quality or 0.0)
    if quality is None:
        return _clamp(popularity)
    share = cfg.quality_popularity_share
    return _clamp(share * popularity + (1.0 - share) * quality)


def context_score(recipe: RecipeCandidate, max_minutes: int | None) -> float | None:
    """조리시간 적합도. 상한이나 조리시간이 없으면 측정 불가입니다."""
    if max_minutes is None or recipe.cook_minutes is None:
        return None
    overrun = max(0.0, (recipe.cook_minutes - max_minutes) / max_minutes)
    return _clamp(1.0 - overrun)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
