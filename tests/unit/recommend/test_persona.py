"""취향 페르소나. 우선순위, 시간 감쇠, 주기 친화도, 합치는 식.

전부 고정 시각으로 잽니다. 이 파일의 어떤 검사도 시계를 보지 않습니다.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from features.recommend.engine import persona
from features.recommend.engine.persona import (
    Persona,
    PersonaSource,
    TasteEvent,
    TasteProfile,
    cold_persona,
    derive_persona,
)
from features.recommend.enums import EventType, UserMode
from features.recommend.policy import RankingPolicy

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=KST)
DAY = timedelta(days=1)

FULL = (1.0,) * 6
EMPTY = (0.0,) * 6
#: 시간 요소를 전부 끈 정책. 합치는 식만 봅니다.
STATIC = replace(
    RankingPolicy(),
    persona_half_life_days=0.0,
    season_cycle_strength=0.0,
    weekly_cycle_strength=0.0,
    daily_cycle_strength=0.0,
)


def cook(
    flavor: tuple[float, ...], at: datetime = NOW, kind: EventType = EventType.COOK
) -> TasteEvent:
    return TasteEvent(recipe_id=1, kind=kind, at=at, flavor=flavor)


# ─────────────────────────────────────────────────────────────────
# 사전 취향과 우선순위
# ─────────────────────────────────────────────────────────────────
def test_prior_from_picks_is_the_axis_mean() -> None:
    picks = ((0.2, 0.4, 0.6, None, 1.0, 0.0), (0.4, 0.4, 0.0, 0.5, None, 0.2))
    assert persona.prior_from_picks(picks) == pytest.approx((0.3, 0.4, 0.3, 0.5, 1.0, 0.1))
    assert persona.prior_from_picks(()) is None


def test_prior_from_scales_fills_only_the_first_three_axes() -> None:
    """순서는 데이터 파트 계약과 같이 [매움, 짠맛, 단맛] 이고 뒤 3축은 모릅니다."""
    assert persona.prior_from_scales((1.0, 0.5, 0.0)) == (1.0, 0.5, 0.0, None, None, None)
    assert persona.prior_from_scales(None) is None
    with pytest.raises(ValueError):
        persona.prior_from_scales((2.0, 0.5, 0.0))


def test_picks_win_and_scales_are_stored_but_unused() -> None:
    """회의 결정: 고른 음식이 있으면 직접 적은 3축은 계산에 쓰지 않습니다."""
    with_picks = TasteProfile(user_id=1, pick_flavors=(FULL,), scales=(0.0, 0.0, 0.0))
    other_scales = replace(with_picks, scales=(1.0, 1.0, 1.0))

    left = derive_persona(with_picks, NOW, STATIC)
    right = derive_persona(other_scales, NOW, STATIC)

    assert left.prior_source is PersonaSource.PICKS
    assert left.vec == right.vec == FULL
    assert left.prior_weight == STATIC.picks_prior_weight


def test_scales_are_the_fallback_without_picks() -> None:
    result = derive_persona(TasteProfile(user_id=1, scales=(0.25, 0.5, 0.75)), NOW, STATIC)
    assert result.prior_source is PersonaSource.SCALES
    assert result.vec == (0.25, 0.5, 0.75, None, None, None)
    assert result.prior_weight == STATIC.scales_prior_weight


def test_nothing_known_is_cold() -> None:
    result = derive_persona(TasteProfile(user_id=1), NOW, STATIC)
    assert result.is_cold
    assert result.prior_source is PersonaSource.NONE
    assert result.mode is UserMode.COLD
    assert result.vec == (None,) * 6
    assert result == cold_persona()


def test_prior_alone_when_there_are_no_events() -> None:
    result = derive_persona(TasteProfile(user_id=1, pick_flavors=(EMPTY, FULL)), NOW, STATIC)
    assert result.vec == pytest.approx((0.5,) * 6)
    assert result.mode is UserMode.COLD
    assert result.n_events == 0 and result.behavior_weight == 0.0


# ─────────────────────────────────────────────────────────────────
# 합치는 식
# ─────────────────────────────────────────────────────────────────
def test_one_event_pulls_by_its_weight_against_the_prior() -> None:
    """persona = (k·p + S·b) / (k + S). k=12, p=0, S=1, b=1 → 1/13."""
    profile = TasteProfile(user_id=1, pick_flavors=(EMPTY,), events=(cook(FULL),))
    result = derive_persona(profile, NOW, STATIC)
    assert result.vec == pytest.approx((1.0 / 13.0,) * 6)
    assert result.mode is UserMode.BLENDED


def test_enough_events_make_behavior_dominate() -> None:
    events = tuple(cook(FULL) for _ in range(120))
    profile = TasteProfile(user_id=1, pick_flavors=(EMPTY,), events=events)
    result = derive_persona(profile, NOW, STATIC)
    assert result.vec[0] == pytest.approx(120.0 / 132.0)
    assert result.mode is UserMode.WARM
    assert result.behavior_weight == pytest.approx(120.0)


def test_axes_only_events_know_come_from_behavior_alone() -> None:
    """3축 척도 사용자에게 조리 이벤트가 오면 뒤 3축은 행동만으로 채워집니다."""
    profile = TasteProfile(
        user_id=1,
        scales=(0.5, 0.5, 0.5),
        events=(cook((0.9, 0.9, 0.9, 0.2, 0.3, 0.4)),),
    )
    result = derive_persona(profile, NOW, STATIC)
    front = (6.0 * 0.5 + 1.0 * 0.9) / 7.0
    assert result.vec == pytest.approx((front, front, front, 0.2, 0.3, 0.4))


def test_mode_moves_from_onboarding_to_blended_to_behavior() -> None:
    base = TasteProfile(user_id=1, pick_flavors=(EMPTY,))
    assert derive_persona(base, NOW, STATIC).mode is UserMode.COLD
    one = replace(base, events=(cook(FULL),))
    assert derive_persona(one, NOW, STATIC).mode is UserMode.BLENDED
    enough = replace(base, events=tuple(cook(FULL) for _ in range(12)))
    assert derive_persona(enough, NOW, STATIC).mode is UserMode.WARM


def test_event_order_does_not_matter() -> None:
    events = [cook((float(i % 2),) * 6, NOW - i * DAY) for i in range(10)]
    forward = TasteProfile(user_id=1, events=tuple(events))
    shuffled = list(events)
    random.Random(7).shuffle(shuffled)  # noqa: S311  # 순서 무관성 검사용. 암호 용도가 아닙니다
    backward = TasteProfile(user_id=1, events=tuple(shuffled))
    assert derive_persona(forward, NOW, RankingPolicy()).vec == pytest.approx(
        derive_persona(backward, NOW, RankingPolicy()).vec
    )


# ─────────────────────────────────────────────────────────────────
# 이벤트 종류
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", [EventType.DISMISS, EventType.UNSAVE, EventType.IMPRESSION])
def test_negative_and_zero_kinds_do_not_build_taste(kind: EventType) -> None:
    profile = TasteProfile(user_id=1, events=(cook(FULL, kind=kind),))
    result = derive_persona(profile, NOW, STATIC)
    assert result.n_events == 0 and result.is_cold


def test_rating_uses_the_label_mapping_clipped_at_zero() -> None:
    assert persona.kind_weight(EventType.RATING, 5.0) == pytest.approx(1.0)
    assert persona.kind_weight(EventType.RATING, 4.0) == pytest.approx(0.5)
    assert persona.kind_weight(EventType.RATING, 1.0) == 0.0
    assert persona.kind_weight(EventType.RATING, None) == 0.0
    assert persona.kind_weight(EventType.COOK, None) == 1.0
    assert persona.kind_weight(EventType.CLICK, None) == pytest.approx(0.3)


# ─────────────────────────────────────────────────────────────────
# 시간 감쇠
# ─────────────────────────────────────────────────────────────────
def test_half_life_halves_the_weight() -> None:
    policy = replace(STATIC, persona_half_life_days=90.0)
    events = (cook(FULL, NOW), cook(EMPTY, NOW - 90 * DAY))
    result = derive_persona(TasteProfile(user_id=1, events=events), NOW, policy)
    assert result.vec[0] == pytest.approx(1.0 / 1.5)
    assert result.behavior_weight == pytest.approx(1.5)


def test_decay_off_when_the_half_life_is_zero() -> None:
    events = (cook(FULL, NOW), cook(EMPTY, NOW - 90 * DAY))
    result = derive_persona(TasteProfile(user_id=1, events=events), NOW, STATIC)
    assert result.vec[0] == pytest.approx(0.5)


def test_future_events_are_not_boosted() -> None:
    policy = replace(STATIC, persona_half_life_days=90.0)
    assert persona.event_weight(cook(FULL, NOW + 10 * DAY), NOW, policy) == pytest.approx(1.0)


def test_old_events_fade_back_to_the_prior() -> None:
    """잊는 것이 식에서 나옵니다. 1년 전 매운 조리는 반감기 90일이면 6% 만 남습니다."""
    policy = replace(STATIC, persona_half_life_days=90.0)
    prior = TasteProfile(user_id=1, pick_flavors=(EMPTY,))
    recent = replace(prior, events=tuple(cook(FULL, NOW - i * DAY) for i in range(16)))
    stale = replace(prior, events=tuple(cook(FULL, NOW - 365 * DAY - i * DAY) for i in range(16)))
    assert derive_persona(recent, NOW, policy).vec[0] > 0.5
    assert derive_persona(stale, NOW, policy).vec[0] < 0.1


# ─────────────────────────────────────────────────────────────────
# 주기 친화도
# ─────────────────────────────────────────────────────────────────
def test_cycle_affinity_shape() -> None:
    assert persona.cycle_affinity(0.0, 0.5) == pytest.approx(1.0)
    assert persona.cycle_affinity(0.5, 0.5) == pytest.approx(0.5)
    assert persona.cycle_affinity(0.5, 1.0) == pytest.approx(0.0)
    assert persona.cycle_affinity(0.25, 0.0) == pytest.approx(1.0)
    assert persona.cycle_affinity(0.25, 1.0) == pytest.approx(persona.cycle_affinity(-0.25, 1.0))


def test_same_season_counts_and_the_opposite_season_barely_does() -> None:
    policy = replace(STATIC, season_cycle_strength=1.0)
    last_year = cook(FULL, datetime(2025, 9, 11, 12, 0, tzinfo=KST))
    half_year = cook(EMPTY, datetime(2026, 3, 13, 12, 0, tzinfo=KST))
    result = derive_persona(TasteProfile(user_id=1, events=(last_year, half_year)), NOW, policy)
    assert result.vec[0] > 0.999
    assert persona.event_weight(last_year, NOW, policy) == pytest.approx(1.0)
    assert persona.event_weight(half_year, NOW, policy) < 1e-3


def test_cycle_strength_zero_ignores_the_season() -> None:
    last_year = cook(FULL, datetime(2025, 9, 11, 12, 0, tzinfo=KST))
    half_year = cook(EMPTY, datetime(2026, 3, 13, 12, 0, tzinfo=KST))
    result = derive_persona(TasteProfile(user_id=1, events=(last_year, half_year)), NOW, STATIC)
    assert result.vec[0] == pytest.approx(0.5)


def test_weekly_and_daily_cycles_use_weekday_and_hour() -> None:
    weekly = replace(STATIC, weekly_cycle_strength=1.0)
    same_weekday = cook(FULL, NOW - 7 * DAY)
    opposite = cook(FULL, NOW - 3 * DAY - timedelta(hours=12))
    assert persona.event_weight(same_weekday, NOW, weekly) == pytest.approx(1.0)
    assert persona.event_weight(opposite, NOW, weekly) < 1e-3

    daily = replace(STATIC, daily_cycle_strength=1.0)
    same_hour = cook(FULL, NOW - DAY)
    midnight = cook(FULL, NOW - timedelta(hours=12))
    assert persona.event_weight(same_hour, NOW, daily) == pytest.approx(1.0)
    assert persona.event_weight(midnight, NOW, daily) < 1e-3


def test_phase_of_year_uses_the_length_of_that_year() -> None:
    assert persona.phase_of_year(datetime(2026, 1, 1, tzinfo=KST)) == 0.0
    assert persona.phase_of_year(datetime(2026, 7, 2, 12, 0, tzinfo=KST)) == pytest.approx(
        0.5, abs=0.002
    )
    assert persona.phase_of_year(datetime(2028, 7, 2, 0, 0, tzinfo=KST)) == pytest.approx(
        0.5, abs=0.002
    )


# ─────────────────────────────────────────────────────────────────
# 시각
# ─────────────────────────────────────────────────────────────────
def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError):
        TasteEvent(recipe_id=1, kind=EventType.COOK, at=datetime(2026, 9, 11), flavor=FULL)
    with pytest.raises(ValueError):
        derive_persona(TasteProfile(user_id=1), datetime(2026, 9, 11), STATIC)
    with pytest.raises(ValueError):
        TasteProfile(user_id=1, updated_at=datetime(2026, 9, 11))


def test_the_same_instant_in_two_timezones_weighs_the_same() -> None:
    policy = replace(STATIC, persona_half_life_days=90.0)
    in_utc = cook(FULL, datetime(2026, 9, 11, 3, 0, tzinfo=UTC))
    in_kst = cook(FULL, NOW)
    assert persona.event_weight(in_utc, NOW, policy) == pytest.approx(
        persona.event_weight(in_kst, NOW, policy)
    )


def test_flavor_must_have_six_axes() -> None:
    with pytest.raises(ValueError):
        TasteEvent(recipe_id=1, kind=EventType.COOK, at=NOW, flavor=(1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        TasteProfile(user_id=1, scales=(1.0, 1.0))


# ─────────────────────────────────────────────────────────────────
# 잘라내기
# ─────────────────────────────────────────────────────────────────
def test_prune_keeps_the_newest_within_age_and_count() -> None:
    policy = replace(RankingPolicy(), persona_max_events=2, persona_max_event_age_days=30)
    events = (
        cook(FULL, NOW - 1 * DAY),
        cook(FULL, NOW - 2 * DAY),
        cook(FULL, NOW - 40 * DAY),
        cook(FULL, NOW - 3 * DAY),
    )
    kept = persona.prune_events(events, NOW, policy)
    assert [e.at for e in kept] == [NOW - 2 * DAY, NOW - 1 * DAY]


def test_persona_is_a_value() -> None:
    """같은 입력이면 같은 값입니다. 추적에 그대로 실어도 됩니다."""
    profile = TasteProfile(user_id=1, pick_flavors=(FULL,), events=(cook(EMPTY, NOW - DAY),))
    assert derive_persona(profile, NOW, RankingPolicy()) == derive_persona(
        profile, NOW, RankingPolicy()
    )
    assert isinstance(derive_persona(profile, NOW, RankingPolicy()), Persona)
