"""Stage 2. 최근 노출·조리·기피 재료에 대한 곱연산 감점."""

from __future__ import annotations

from dataclasses import replace

from features.recommend.schema import RankConfig, RecipeCandidate, ScoredCandidate, UserContext


def apply_penalties(scored: ScoredCandidate, ctx: UserContext, cfg: RankConfig) -> ScoredCandidate:
    """세 계수를 곱합니다. 뺄셈이면 고득점 레시피가 감점을 흡수해 계속 상위를 차지합니다."""
    recipe = scored.candidate
    factor = (
        recent_factor(recipe.recipe_id, ctx, cfg)
        * cooked_factor(recipe.recipe_id, ctx, cfg)
        * (1.0 - avoid_penalty(recipe, ctx, cfg))
    )
    return replace(scored, score=scored.base_score * factor)


def recent_factor(recipe_id: int, ctx: UserContext, cfg: RankConfig) -> float:
    """최근 7일 안에 추천으로 노출된 레시피입니다."""
    return cfg.penalty_recent if recipe_id in ctx.history.recent_recipe_ids else 1.0


def cooked_factor(recipe_id: int, ctx: UserContext, cfg: RankConfig) -> float:
    """최근 14일 안에 조리를 마친 레시피입니다."""
    return cfg.penalty_cooked if recipe_id in ctx.history.cooked_recipe_ids else 1.0


def avoid_penalty(recipe: RecipeCandidate, ctx: UserContext, cfg: RankConfig) -> float:
    """기피 재료 비율에 배수를 곱하되 상한을 둡니다.

    상한이 없으면 감점이 1 을 넘어 점수가 음수가 됩니다.
    """
    if not recipe.all_ids:
        return 0.0
    ratio = len(recipe.all_ids & ctx.history.avoid_ingredient_ids) / len(recipe.all_ids)
    return min(cfg.avoid_cap, cfg.avoid_multiplier * ratio)
