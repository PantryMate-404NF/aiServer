"""Stage 1. 조회된 레시피를 후보군으로 거르고, 모자라면 세 단계로 완화합니다.

SQL 은 repository 에 있습니다. 여기서는 그 결과(또는 Mock 풀)를 같은 조건으로 다시 걸러
알레르기 하드컷을 DB 밖에서도 보장하고, 후보가 부족할 때의 완화 순서를 정합니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from features.recommend.schema import RankConfig, RecipeCandidate, UserContext

FALLBACK_NONE = "none"
FALLBACK_RELAX_MISSING = "relax_missing"
FALLBACK_SUBSTITUTE = "substitute"
FALLBACK_POPULARITY = "popularity"


@dataclass(frozen=True)
class CandidateSet:
    """후보군과 어느 단계의 완화를 거쳤는지."""

    candidates: tuple[RecipeCandidate, ...]
    fallback_stage: str
    max_missing: int

    @property
    def degraded(self) -> bool:
        return self.fallback_stage != FALLBACK_NONE


def missing_ids(recipe: RecipeCandidate, pantry: frozenset[int]) -> tuple[int, ...]:
    """보유하지 않은 필수 재료. 응답에 그대로 실리므로 정렬해 둡니다."""
    return tuple(sorted(recipe.essential_ids - pantry))


def is_eligible(
    recipe: RecipeCandidate,
    ctx: UserContext,
    *,
    pantry: frozenset[int],
    max_missing: int,
) -> bool:
    """repository 의 WHERE 절과 같은 조건입니다. 둘이 어긋나면 이쪽 테스트가 먼저 깨져야 합니다."""
    if recipe.all_ids & ctx.history.allergy_ingredient_ids:
        return False
    if recipe.essential_ids and not (recipe.essential_ids & pantry):
        return False
    if len(recipe.essential_ids - pantry) > max_missing:
        return False
    limit = ctx.max_cook_minutes
    return limit is None or recipe.cook_minutes is None or recipe.cook_minutes <= limit


def retrieve(
    pool: Iterable[RecipeCandidate],
    ctx: UserContext,
    *,
    pantry: frozenset[int],
    max_missing: int,
    limit: int,
) -> list[RecipeCandidate]:
    """조건을 만족하는 레시피를 부족 재료 수 오름차순, 인기 내림차순으로 최대 limit 건."""
    eligible = [r for r in pool if is_eligible(r, ctx, pantry=pantry, max_missing=max_missing)]
    eligible.sort(
        key=lambda r: (len(r.essential_ids - pantry), -(r.popularity_score or 0.0), r.recipe_id)
    )
    return eligible[:limit]


def select_candidates(
    pool: Sequence[RecipeCandidate], ctx: UserContext, cfg: RankConfig
) -> CandidateSet:
    """후보가 부족하면 부족수 완화, 대체재 확장, 인기순 순으로 넓힙니다.

    어느 단계에서도 알레르기 재료가 든 레시피는 돌아오지 않습니다.
    """
    # min_candidates 는 Top-20 을 전제한 값입니다. 더 달라면 그만큼은 있어야 합니다.
    needed = max(cfg.min_candidates, ctx.top_k)
    for max_missing in range(cfg.max_missing, cfg.max_missing_relaxed + 1):
        found = retrieve(
            pool, ctx, pantry=ctx.pantry_ids, max_missing=max_missing, limit=cfg.candidate_limit
        )
        if len(found) >= needed:
            stage = FALLBACK_NONE if max_missing == cfg.max_missing else FALLBACK_RELAX_MISSING
            return CandidateSet(tuple(found), stage, max_missing)

    substitutes = ctx.history.substitute_ids
    if substitutes:
        found = retrieve(
            pool,
            ctx,
            pantry=ctx.pantry_ids | substitutes,
            max_missing=cfg.max_missing_relaxed,
            limit=cfg.candidate_limit,
        )
        if len(found) >= needed:
            return CandidateSet(tuple(found), FALLBACK_SUBSTITUTE, cfg.max_missing_relaxed)

    popular = [r for r in pool if not (r.all_ids & ctx.history.allergy_ingredient_ids)]
    popular.sort(key=lambda r: (-(r.popularity_score or 0.0), r.recipe_id))
    return CandidateSet(
        tuple(popular[: cfg.candidate_limit]), FALLBACK_POPULARITY, cfg.max_missing_relaxed
    )
