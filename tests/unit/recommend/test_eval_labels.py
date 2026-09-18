"""정답 라벨. 이벤트를 레시피별 gain 으로 바꿉니다 (명세 2.3)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from features.recommend.enums import LABEL_WEIGHT, EventType
from features.recommend.evaluation.labels import LABEL_WINDOW_DAYS, cooked_recipes, gains
from features.recommend.evaluation.record import EvalEvent

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))


def _event(
    recipe_id: int, kind: EventType, *, days: float = 0.0, value: float | None = None
) -> EvalEvent:
    return EvalEvent(
        recipe_id=recipe_id,
        event_type=kind,
        value=value,
        position=None,
        created_at=NOW + timedelta(days=days),
    )


def test_every_served_recipe_gets_a_gain_and_impression_is_zero(
    make_record: Callable[..., Any],
) -> None:
    record = make_record(events=[_event(101, EventType.IMPRESSION), _event(102, EventType.CLICK)])

    result = gains(record)

    assert result == {101: 0.0, 102: LABEL_WEIGHT[EventType.CLICK], 103: 0.0, 104: 0.0, 105: 0.0}


def test_multiple_events_take_the_maximum(make_record: Callable[..., Any]) -> None:
    record = make_record(
        events=[
            _event(101, EventType.CLICK),
            _event(101, EventType.COOK),
            _event(101, EventType.SAVE),
        ]
    )
    assert gains(record)[101] == LABEL_WEIGHT[EventType.COOK]


def test_negative_events_clamp_to_zero(make_record: Callable[..., Any]) -> None:
    record = make_record(events=[_event(101, EventType.DISMISS), _event(102, EventType.UNSAVE)])
    assert gains(record)[101] == 0.0
    assert gains(record)[102] == 0.0


def test_rating_maps_through_rating_to_label(make_record: Callable[..., Any]) -> None:
    record = make_record(
        events=[_event(101, EventType.RATING, value=5.0), _event(102, EventType.RATING, value=1.0)]
    )
    assert gains(record)[101] == 1.0
    assert gains(record)[102] == 0.0


def test_events_outside_window_are_ignored(make_record: Callable[..., Any]) -> None:
    record = make_record(
        events=[
            _event(101, EventType.COOK, days=LABEL_WINDOW_DAYS + 0.5),
            _event(102, EventType.COOK, days=-1),
            _event(103, EventType.COOK, days=LABEL_WINDOW_DAYS - 0.5),
        ]
    )
    result = gains(record)
    assert result[101] == 0.0
    assert result[102] == 0.0
    assert result[103] == 1.0


def test_events_for_unserved_recipes_do_not_appear(make_record: Callable[..., Any]) -> None:
    record = make_record(events=[_event(999, EventType.COOK)])
    assert 999 not in gains(record)


def test_user_level_cooked_set_uses_user_events_only(make_record: Callable[..., Any]) -> None:
    """유저 단위 Recall 은 request_id 없이 잇습니다. 두 계열을 합산하지 않습니다."""
    record = make_record(
        events=[_event(101, EventType.COOK)],
        user_events=[
            _event(555, EventType.COOK, days=3),
            _event(556, EventType.SAVE, days=3),
            _event(557, EventType.COOK, days=20),
        ],
    )
    assert cooked_recipes(record) == {555}
    assert 555 not in gains(record)
