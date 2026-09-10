"""② Ranking. 피처 원값을 가중합해 점수를 매기고 감점을 곱합니다. 순수 함수만 둡니다.

정규화는 `raw = Σwᵢfᵢ / Σwᵢ` 이며 **fᵢ 가 None 인 피처는 분자와 분모에서 함께 빠집니다.**
0 으로 채우면 데이터 결측이 감점이 되고, 분모를 고정하면 결측이 많은 레시피가 통째로
불리해집니다. 이 방식은 남은 가중치를 비례 재분배한 것과 수학적으로 같습니다.

감점은 뺄셈이 아니라 곱셈입니다. 뺄셈이면 고득점 레시피가 감점을 흡수해 계속 상위를
차지합니다. `ScoredCandidate.penalty` 에 곱한 값을 그대로 남겨 로그에서 되계산됩니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.engine.feature import compute_features
from features.recommend.enums import DEFAULT_WEIGHTS
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate, ScoredCandidate


def weighted_score(features: Mapping[str, float | None], weights: Mapping[str, float]) -> float:
    """측정 가능한 피처만으로 가중 평균을 냅니다. 하나도 없으면 0 입니다."""
    numerator = 0.0
    denominator = 0.0
    for key, value in features.items():
        weight = weights.get(key, 0.0)
        if value is None or weight <= 0.0:
            continue
        numerator += weight * value
        denominator += weight
    if denominator <= 0.0:
        return 0.0
    return _clamp(numerator / denominator)


def penalty_factor(
    recipe_id: int, recipe: RecipeFeature, ctx: UserContext, policy: RankingPolicy
) -> float:
    """최근 노출·조리·기피 재료 세 계수의 곱. 0~1 입니다."""
    recent = policy.penalty_recent if recipe_id in ctx.history.recent_recipe_ids else 1.0
    cooked = policy.penalty_cooked if recipe_id in ctx.history.cooked_recipe_ids else 1.0
    return _clamp(recent * cooked * (1.0 - avoid_penalty(recipe, ctx, policy)))


def avoid_penalty(recipe: RecipeFeature, ctx: UserContext, policy: RankingPolicy) -> float:
    """기피 재료 비율에 배수를 곱하되 상한을 둡니다.

    상한이 없으면 기피 비율 50% 부터 감점이 1 을 넘어 점수가 음수가 됩니다.
    """
    if not recipe.all_ids:
        return 0.0
    ratio = len(recipe.all_ids & ctx.history.avoid_ingredient_ids) / len(recipe.all_ids)
    return min(policy.avoid_cap, policy.avoid_multiplier * ratio)


def score_candidate(
    candidate: Candidate,
    recipe: RecipeFeature,
    ctx: UserContext,
    corpus: CorpusStats,
    policy: RankingPolicy,
    weights: Mapping[str, float] | None = None,
    *,
    max_missing: int | None = None,
) -> ScoredCandidate:
    """후보 하나를 ② 산출로 바꿉니다. 제외는 하지 않습니다 — 그것은 ① 의 일입니다."""
    features = compute_features(
        candidate,
        recipe,
        ctx,
        corpus,
        max_missing=policy.max_missing if max_missing is None else max_missing,
        taste_min_norm=policy.taste_min_norm,
    )
    effective = dict(weights or DEFAULT_WEIGHTS)
    penalty = penalty_factor(candidate.recipe_id, recipe, ctx, policy)
    return ScoredCandidate(
        recipe_id=candidate.recipe_id,
        missing_count=candidate.missing_count,
        missing_ids=list(candidate.missing_ids),
        coverage=candidate.coverage,
        cluster_id=candidate.cluster_id,
        features=features,
        score=round(weighted_score(features, effective) * penalty, 6),
        penalty=round(penalty, 6),
    )


def score_all(
    candidates: Sequence[Candidate],
    recipes: Mapping[int, RecipeFeature],
    ctx: UserContext,
    corpus: CorpusStats,
    policy: RankingPolicy,
    weights: Mapping[str, float] | None = None,
    *,
    max_missing: int | None = None,
) -> list[ScoredCandidate]:
    """후보 전체를 점수 내림차순으로. 같은 점수면 recipe_id 순으로 고정합니다.

    `recipes` 에 없는 후보는 레시피 피처를 못 읽은 것이므로 빈 피처로 채웁니다.
    빼지 않는 이유는 ① 이 이미 고른 후보를 ② 가 다시 거르면 왜 빠졌는지를 두 곳에서
    찾아야 하기 때문입니다 (`stage.py` 책임 경계).
    """
    scored = [
        score_candidate(
            candidate,
            recipes.get(candidate.recipe_id, RecipeFeature(recipe_id=candidate.recipe_id)),
            ctx,
            corpus,
            policy,
            weights,
            max_missing=max_missing,
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda item: (-item.score, item.recipe_id))
    return scored


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
