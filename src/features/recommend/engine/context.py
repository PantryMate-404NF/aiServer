"""입력 어댑터. 요청과 사용자 이력을 엔진이 받는 표준 문맥으로 합칩니다."""

from __future__ import annotations

from features.recommend.engine import feedback
from features.recommend.schema import RecommendRequest
from features.recommend.stage import RankConfig, UserContext, UserHistory


def build_context(request: RecommendRequest, history: UserHistory, cfg: RankConfig) -> UserContext:
    """요청 필드를 집합으로 바꾸고 온보딩 취향과 행동 취향을 섞습니다. 엔진은 이 결과만 봅니다."""
    taste = feedback.effective_taste(
        request.taste_preference.as_vector(),
        history.behavior_taste_vec,
        history.events_count,
        cfg,
    )
    return UserContext(
        user_id=request.user_id,
        pantry_ids=frozenset(request.pantry_ingredient_ids),
        expiring_ids=frozenset(request.expiring_ingredient_ids),
        taste_vec=taste,
        top_k=request.top_k,
        household_size=request.household_size,
        preferred_cuisines=frozenset(request.preferred_cuisines),
        max_cook_minutes=request.max_cook_minutes,
        history=history,
    )
