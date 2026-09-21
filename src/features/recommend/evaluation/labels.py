"""이벤트를 정답 gain 으로 (명세 2.3).

가중치는 `enums.LABEL_WEIGHT` 를 그대로 씁니다. 평가 계층이 같은 것을 다시 정의하지 않습니다.
같은 레시피에 이벤트가 여럿이면 최댓값, 음수는 0 으로 절단, 창은 추천 뒤 14일입니다.
유저 단위 Recall 만 `user_events` 를 쓰고 두 계열의 라벨을 합산하지 않습니다.
"""

from __future__ import annotations

from datetime import timedelta

from features.recommend.enums import LABEL_WEIGHT, EventType, rating_to_label
from features.recommend.evaluation.record import EvalEvent, EvalRecord

#: 추천 뒤 이 안의 반응만 정답입니다(명세 2.3). 엔진의 최근 조리 감점 창(14일)과 같은 뜻이지만
#: 그 창은 SQL 에만 있어 코드로 잇지 못합니다.
# ponytail: 엔진이 창을 정책 상수로 올리면 여기서 그것을 읽습니다
LABEL_WINDOW_DAYS = 14


def _in_window(record: EvalRecord, event: EvalEvent) -> bool:
    delta = event.created_at - record.created_at
    return timedelta(0) <= delta <= timedelta(days=LABEL_WINDOW_DAYS)


def _weight(event: EvalEvent) -> float:
    if event.event_type == EventType.RATING:
        return rating_to_label(event.value) if event.value is not None else 0.0
    return LABEL_WEIGHT[event.event_type]


def gains(record: EvalRecord) -> dict[int, float]:
    """노출된 레시피마다 gain. 이벤트가 없으면 0.0 이고 노출되지 않은 레시피는 싣지 않습니다."""
    result = {item.recipe_id: 0.0 for item in record.items}
    for event in record.events:
        if event.recipe_id in result and _in_window(record, event):
            result[event.recipe_id] = max(result[event.recipe_id], _weight(event))
    return result


def cooked_recipes(record: EvalRecord) -> set[int]:
    """추천 뒤 창 안에 그 유저가 조리한 레시피 전체. 유저 단위 Recall 전용입니다."""
    return {
        event.recipe_id
        for event in record.user_events
        if event.event_type == EventType.COOK and _in_window(record, event)
    }
