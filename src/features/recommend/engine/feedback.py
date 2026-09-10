"""동적 페르소나. 이벤트로 행동 취향 벡터를 지수 이동 평균으로 갱신하고 온보딩 취향과 섞습니다."""

from __future__ import annotations

from features.recommend.schema import EventType, FlavorVector
from features.recommend.stage import RankConfig

# 취향 벡터를 움직이는 이벤트. dismiss 는 어느 맛이 싫은지 말해 주지 않으므로 반영하지 않습니다.
TASTE_EVENTS: frozenset[str] = frozenset({"click", "cook"})
# user_vector 테이블의 기본값과 같습니다. 이력이 없는 사용자의 시작점입니다.
DEFAULT_BEHAVIOR_VEC: FlavorVector = (0.5, 0.5, 0.5)


def moves_taste(event_type: EventType) -> bool:
    return event_type in TASTE_EVENTS


def update_behavior_vector(
    current: FlavorVector | None, recipe_flavor: FlavorVector, cfg: RankConfig
) -> FlavorVector:
    """행동 벡터를 레시피 맛 쪽으로 gamma 만큼 옮깁니다."""
    base = current if current is not None else DEFAULT_BEHAVIOR_VEC
    gamma = cfg.ema_gamma
    x, y, z = (
        (1.0 - gamma) * before + gamma * after
        for before, after in zip(base, recipe_flavor, strict=True)
    )
    return (x, y, z)


def effective_taste(
    onboarding: FlavorVector,
    behavior: FlavorVector | None,
    events_count: int,
    cfg: RankConfig,
) -> FlavorVector:
    """이벤트 수가 warm_event_count 에 이를 때까지 온보딩에서 행동 쪽으로 선형 전이합니다."""
    if behavior is None or events_count <= 0:
        return onboarding
    alpha = min(1.0, events_count / cfg.warm_event_count)
    x, y, z = (
        (1.0 - alpha) * cold + alpha * warm for cold, warm in zip(onboarding, behavior, strict=True)
    )
    return (x, y, z)
