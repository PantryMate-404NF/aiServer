"""사용자 취향 페르소나. 고른 음식과 행동 이벤트에서 6축 취향을 만듭니다.

사전 취향은 고른 음식의 6축 평균 → 없으면 3축 척도 → 둘 다 없으면 없음 순서이고, 고른 음식이
있으면 척도는 계산에 쓰지 않습니다. 이벤트는 저장된 시각으로 무게를 매겨(반감기 감쇠 x 주기
친화도) 가중 평균을 내고, 축마다 `(k·prior + S·behavior) / (k + S)` 로 사전 취향과 합칩니다.
근거와 식의 뜻은 `docs/decisions/2026-09-11_taste_persona_from_picks_with_time_decay.md` 입니다.

이 파일은 시계를 보지 않습니다. `now` 는 호출자가 넘기고, 같은 입력이면 결과가 같습니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from features.recommend.engine import taste
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import LABEL_WEIGHT, EventType, UserMode, rating_to_label
from features.recommend.policy import RankingPolicy

SECONDS_PER_DAY = 86_400.0
HOURS_PER_DAY = 24.0
DAYS_PER_WEEK = 7.0
#: 직접 적는 3축 척도의 축 수. 순서는 데이터 파트 계약과 같이 [매움, 짠맛, 단맛] 입니다.
SCALE_AXIS_COUNT = 3


class PersonaSource(StrEnum):
    """사전 취향이 어디서 왔는가. 추천 로그의 `params.persona_source` 에 실립니다."""

    PICKS = "picks"
    SCALES = "scales"
    NONE = "none"


@dataclass(frozen=True)
class TasteEvent:
    """사용자가 레시피에 한 행동 하나. 6축은 기록 시점의 스냅숏입니다."""

    recipe_id: int
    kind: EventType
    at: datetime
    flavor: FlavorVector
    #: 별점처럼 값이 따라오는 종류만 씁니다.
    value: float | None = None

    def __post_init__(self) -> None:
        _require_aware(self.at, "TasteEvent.at")
        if len(self.flavor) != taste.AXIS_COUNT:
            raise ValueError(f"flavor 는 {taste.AXIS_COUNT}축이어야 합니다: {len(self.flavor)}")


@dataclass(frozen=True)
class TasteProfile:
    """사용자 한 명의 취향 원본. 저장소가 이 형태로 읽고 씁니다.

    계산 결과가 아니라 **원본**을 둡니다. 평균만 남기면 시드가 바뀌었을 때 다시 계산할 수
    없습니다 — 데이터 파트가 `user_vector.onboarding_picks` 에 원본을 두는 이유와 같습니다.
    """

    user_id: int
    #: 온보딩 제시 목록에서 고른 것의 인덱스. 재계산의 열쇠입니다.
    picks: tuple[int, ...] = ()
    #: 고른 음식들의 6축. 저장 시점의 스냅숏이라 이 파일만으로 계산이 끝납니다.
    pick_flavors: tuple[FlavorVector, ...] = ()
    #: 직접 적은 3축 척도 원본 [매움, 짠맛, 단맛]. 고른 음식이 있으면 계산에 쓰지 않습니다.
    scales: tuple[float, ...] | None = None
    events: tuple[TasteEvent, ...] = ()
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.scales is not None and len(self.scales) != SCALE_AXIS_COUNT:
            raise ValueError(f"scales 는 {SCALE_AXIS_COUNT}축이어야 합니다: {len(self.scales)}")
        if self.updated_at is not None:
            _require_aware(self.updated_at, "TasteProfile.updated_at")


@dataclass(frozen=True)
class Persona:
    """계산된 취향. 랭킹은 `vec` 만 보고, 로그와 탐색 정책은 나머지를 봅니다."""

    vec: FlavorVector
    prior_source: PersonaSource
    mode: UserMode
    #: 실제로 적용한 사전 취향의 무게. 사전 취향이 없으면 0 입니다.
    prior_weight: float
    #: 무게를 매긴 뒤의 이벤트 무게 합. 감쇠와 주기가 반영된 값입니다.
    behavior_weight: float
    #: 무게가 0 보다 커서 계산에 들어간 이벤트 수.
    n_events: int
    #: 축마다 이벤트 무게 합. 값이 없는 축은 0 입니다.
    axis_weights: tuple[float, ...] = field(default=(0.0,) * taste.AXIS_COUNT)

    @property
    def is_cold(self) -> bool:
        """취향을 전혀 모릅니다. 탐색 비율을 올려 다양한 목록을 냅니다."""
        return all(v is None for v in self.vec)


def cold_persona() -> Persona:
    """취향 정보가 하나도 없는 사용자."""
    return Persona(
        vec=(None,) * taste.AXIS_COUNT,
        prior_source=PersonaSource.NONE,
        mode=UserMode.COLD,
        prior_weight=0.0,
        behavior_weight=0.0,
        n_events=0,
    )


# ─────────────────────────────────────────────────────────────────
# 사전 취향
# ─────────────────────────────────────────────────────────────────
def prior_from_picks(pick_flavors: tuple[FlavorVector, ...]) -> FlavorVector | None:
    """고른 음식들의 축별 평균. 값이 있는 것만 셉니다. 고른 것이 없으면 None 입니다."""
    if not pick_flavors:
        return None
    axes: list[float | None] = []
    for i in range(taste.AXIS_COUNT):
        values = [x for v in pick_flavors if (x := v[i]) is not None]
        axes.append(sum(values) / len(values) if values else None)
    return tuple(axes)


def prior_from_scales(scales: tuple[float, ...] | None) -> FlavorVector | None:
    """0~1 로 정규화된 3축 척도를 앞 3축에 놓고 뒤 3축은 비웁니다.

    범위 변환(계약의 0~4 등)은 서비스 층이 합니다. 여기는 이미 0~1 인 값만 받습니다.
    """
    if scales is None:
        return None
    if any(not 0.0 <= s <= 1.0 for s in scales):
        raise ValueError(f"scales 는 0~1 로 정규화된 값이어야 합니다: {scales}")
    return taste.as_vector(tuple(float(s) for s in scales))


# ─────────────────────────────────────────────────────────────────
# 이벤트 무게
# ─────────────────────────────────────────────────────────────────
def kind_weight(kind: EventType, value: float | None) -> float:
    """이벤트 종류의 무게. 음의 신호는 0 입니다 — 페르소나는 좋아한 것만으로 만듭니다."""
    if kind is EventType.RATING:
        return 0.0 if value is None else max(0.0, rating_to_label(value))
    return max(0.0, LABEL_WEIGHT.get(kind, 0.0))


def time_decay(age_days: float, half_life_days: float) -> float:
    """반감기 감쇠. 반감기가 0 이하이면 끕니다. 미래 시각은 지금으로 봅니다."""
    if half_life_days <= 0.0:
        return 1.0
    return math.pow(0.5, max(0.0, age_days) / half_life_days)


def cycle_affinity(delta_phase: float, strength: float) -> float:
    """주기 위상 차이(0~1)에 따른 친화도. 같은 시기면 1, 정반대면 1 - strength 입니다."""
    if strength <= 0.0:
        return 1.0
    return 1.0 - strength * (1.0 - math.cos(2.0 * math.pi * delta_phase)) / 2.0


def phase_of_year(at: datetime) -> float:
    """연중 경과 비율(0~1). 윤년도 그 해의 길이로 나눕니다."""
    start = datetime(at.year, 1, 1, tzinfo=at.tzinfo)
    end = datetime(at.year + 1, 1, 1, tzinfo=at.tzinfo)
    return float((at - start) / (end - start))


def phase_of_week(at: datetime) -> float:
    """주중 경과 비율(0~1). 월요일 0시가 0 입니다."""
    day_fraction = (at.hour + at.minute / 60.0) / HOURS_PER_DAY
    return (at.weekday() + day_fraction) / DAYS_PER_WEEK


def phase_of_day(at: datetime) -> float:
    """하루 경과 비율(0~1)."""
    return (at.hour * 3600 + at.minute * 60 + at.second) / SECONDS_PER_DAY


def event_weight(event: TasteEvent, now: datetime, policy: RankingPolicy) -> float:
    """이벤트 하나의 무게. 종류 x 시간 감쇠 x 세 주기의 친화도입니다.

    두 시각을 같은 시간대로 맞춘 뒤 계산합니다. 주기 위상은 지역 시각으로 정해지므로
    호출자가 넘긴 `now` 의 시간대를 기준으로 삼습니다.
    """
    _require_aware(now, "now")
    base = kind_weight(event.kind, event.value)
    if base <= 0.0:
        return 0.0
    local_now = now
    local_at = event.at.astimezone(now.tzinfo)
    age_days = (local_now - local_at).total_seconds() / SECONDS_PER_DAY
    weight = base * time_decay(age_days, policy.persona_half_life_days)
    weight *= cycle_affinity(
        phase_of_year(local_at) - phase_of_year(local_now), policy.season_cycle_strength
    )
    weight *= cycle_affinity(
        phase_of_week(local_at) - phase_of_week(local_now), policy.weekly_cycle_strength
    )
    weight *= cycle_affinity(
        phase_of_day(local_at) - phase_of_day(local_now), policy.daily_cycle_strength
    )
    return weight


# ─────────────────────────────────────────────────────────────────
# 합치기
# ─────────────────────────────────────────────────────────────────
def derive_persona(profile: TasteProfile, now: datetime, policy: RankingPolicy) -> Persona:
    """원본에서 페르소나를 만듭니다. 순수 함수이며 같은 입력이면 같은 결과입니다."""
    _require_aware(now, "now")
    prior, source, prior_weight = _prior(profile, policy)
    behavior, axis_weights, total_weight, n_events = _behavior(profile.events, now, policy)

    vec: list[float | None] = []
    for i in range(taste.AXIS_COUNT):
        p = prior[i] if prior is not None else None
        b, s = behavior[i], axis_weights[i]
        if p is None and b is None:
            vec.append(None)
        elif p is None:
            vec.append(b)
        elif b is None or s <= 0.0:
            vec.append(p)
        else:
            vec.append((prior_weight * p + s * b) / (prior_weight + s))

    if n_events == 0:
        mode = UserMode.COLD
    elif prior is None or total_weight >= prior_weight:
        mode = UserMode.WARM
    else:
        mode = UserMode.BLENDED
    return Persona(
        vec=tuple(vec),
        prior_source=source,
        mode=mode,
        prior_weight=prior_weight if prior is not None else 0.0,
        behavior_weight=total_weight,
        n_events=n_events,
        axis_weights=axis_weights,
    )


def prune_events(
    events: tuple[TasteEvent, ...], now: datetime, policy: RankingPolicy
) -> tuple[TasteEvent, ...]:
    """너무 오래됐거나 너무 많은 이벤트를 버립니다. 최신 것부터 남깁니다.

    감쇠가 있으면 오래된 이벤트는 어차피 무게가 0 에 가깝습니다. 버리는 것은 결과가
    아니라 저장 크기를 위한 것입니다.
    """
    _require_aware(now, "now")
    limit_seconds = policy.persona_max_event_age_days * SECONDS_PER_DAY
    fresh = [e for e in events if (now - e.at).total_seconds() <= limit_seconds]
    fresh.sort(key=lambda e: e.at, reverse=True)
    kept = fresh[: policy.persona_max_events]
    kept.sort(key=lambda e: e.at)
    return tuple(kept)


def _prior(
    profile: TasteProfile, policy: RankingPolicy
) -> tuple[FlavorVector | None, PersonaSource, float]:
    """우선순위대로 사전 취향 하나를 고릅니다. 고른 음식이 있으면 척도는 보지 않습니다."""
    from_picks = prior_from_picks(profile.pick_flavors)
    if from_picks is not None:
        return from_picks, PersonaSource.PICKS, policy.picks_prior_weight
    from_scales = prior_from_scales(profile.scales)
    if from_scales is not None:
        return from_scales, PersonaSource.SCALES, policy.scales_prior_weight
    return None, PersonaSource.NONE, 0.0


def _behavior(
    events: tuple[TasteEvent, ...], now: datetime, policy: RankingPolicy
) -> tuple[FlavorVector, tuple[float, ...], float, int]:
    """이벤트의 축별 가중 평균과 무게 합. 값이 없는 축은 None 이고 무게 0 입니다."""
    sums = [0.0] * taste.AXIS_COUNT
    weights = [0.0] * taste.AXIS_COUNT
    total = 0.0
    counted = 0
    for event in events:
        w = event_weight(event, now, policy)
        if w <= 0.0:
            continue
        counted += 1
        total += w
        for i, v in enumerate(event.flavor):
            if v is not None:
                sums[i] += w * v
                weights[i] += w
    vec = tuple(sums[i] / weights[i] if weights[i] > 0.0 else None for i in range(taste.AXIS_COUNT))
    return vec, tuple(weights), total, counted


def _require_aware(moment: datetime, label: str) -> None:
    """시간대 없는 시각은 거부합니다. 섞이면 감쇠가 조용히 몇 시간씩 틀립니다."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{label} 은 시간대가 있는 datetime 이어야 합니다: {moment!r}")
