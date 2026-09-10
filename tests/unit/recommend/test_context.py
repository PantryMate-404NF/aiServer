"""입력 어댑터. 비정형 요청과 이력이 하나의 문맥으로 합쳐지는지 봅니다."""

from __future__ import annotations

import pytest

from features.recommend.engine.context import build_context
from features.recommend.schema import RankConfig, RecommendRequest, UserHistory


def test_build_context_blends_history_into_taste(cfg: RankConfig) -> None:
    request = RecommendRequest.model_validate(
        {
            "user_id": 1,
            "pantry_ingredient_ids": [1, 1, 2],
            "expiring_ingredient_ids": [2],
            "taste_preference": {"spicy_level": 4, "sweet_level": 0, "salty_level": 0},
            "household_size": "4인 가구",
            "preferred_cuisines": "한식, 양식",
            "max_cook_minutes": 30,
        }
    )
    history = UserHistory(
        behavior_taste_vec=(0.0, 1.0, 1.0), events_count=cfg.warm_event_count // 2
    )

    ctx = build_context(request, history, cfg)

    assert ctx.pantry_ids == frozenset({1, 2})
    assert ctx.expiring_ids == frozenset({2})
    assert ctx.taste_vec == pytest.approx((0.5, 0.5, 0.5))
    assert ctx.household_size == 4
    assert ctx.preferred_cuisines == frozenset({"한식", "양식"})
    assert ctx.max_cook_minutes == 30
    assert ctx.history is history


def test_build_context_without_history_uses_onboarding_taste(cfg: RankConfig) -> None:
    request = RecommendRequest.model_validate(
        {
            "user_id": 1,
            "pantry_ingredient_ids": [1],
            "taste_preference": {"spicy_level": 4, "sweet_level": 0, "salty_level": 2},
        }
    )

    ctx = build_context(request, UserHistory(), cfg)

    assert ctx.taste_vec == (1.0, 0.5, 0.0)
    assert ctx.top_k == 20
    assert ctx.max_cook_minutes is None
