"""피드백 루프. EMA 갱신과 콜드에서 웜으로의 전이."""

from __future__ import annotations

import pytest

from features.recommend.engine.feedback import (
    DEFAULT_BEHAVIOR_VEC,
    effective_taste,
    moves_taste,
    update_behavior_vector,
)
from features.recommend.stage import RankConfig

ONBOARDING = (1.0, 0.0, 0.0)
BEHAVIOR = (0.0, 1.0, 1.0)


def test_twenty_events_reach_full_behavior_weight(cfg: RankConfig) -> None:
    assert effective_taste(ONBOARDING, BEHAVIOR, cfg.warm_event_count, cfg) == pytest.approx(
        BEHAVIOR
    )
    assert effective_taste(ONBOARDING, BEHAVIOR, cfg.warm_event_count * 5, cfg) == pytest.approx(
        BEHAVIOR
    )


def test_halfway_events_blend_evenly(cfg: RankConfig) -> None:
    assert effective_taste(ONBOARDING, BEHAVIOR, cfg.warm_event_count // 2, cfg) == pytest.approx(
        (0.5, 0.5, 0.5)
    )


def test_cold_start_uses_onboarding_only(cfg: RankConfig) -> None:
    assert effective_taste(ONBOARDING, BEHAVIOR, 0, cfg) == ONBOARDING
    assert effective_taste(ONBOARDING, None, 10, cfg) == ONBOARDING


def test_ema_step_moves_toward_the_recipe(cfg: RankConfig) -> None:
    updated = update_behavior_vector((0.5, 0.5, 0.5), (1.0, 0.0, 0.5), cfg)

    assert updated == pytest.approx((0.6, 0.4, 0.5))


def test_first_event_starts_from_the_table_default(cfg: RankConfig) -> None:
    recipe = (1.0, 0.0, 0.5)

    assert update_behavior_vector(None, recipe, cfg) == update_behavior_vector(
        DEFAULT_BEHAVIOR_VEC, recipe, cfg
    )


def test_dismiss_does_not_move_taste() -> None:
    assert moves_taste("cook")
    assert moves_taste("click")
    assert not moves_taste("dismiss")
