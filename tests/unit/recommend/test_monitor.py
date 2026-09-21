"""추천 엔진의 관측 (`evaluation/monitor.py`). 등록부가 전역이라 값은 전후 차이로 봅니다."""

from __future__ import annotations

import pytest

from features.recommend.engine import mock
from features.recommend.enums import FEATURE_KEYS, UNAVAILABLE_FEATURES, EventType
from features.recommend.evaluation import monitor
from features.recommend.policy import POLICY_ID
from features.recommend.schema import (
    EventAck,
    EventBatchIn,
    EventIn,
    OnboardingOut,
    RecommendRequest,
    RecommendResponse,
    TasteOut,
)
from utils.metrics import REGISTRY

#: 라벨로 써도 되는 이름 전부. 여기 없는 라벨이 생기면 그것이 가짓수가 정해진 값인지부터 봅니다.
ALLOWED_LABELS = {
    "engine",
    "user_mode",
    "degraded",
    "stage",
    "reason",
    "slot",
    "feature",
    "state",
    "source",
    "known",
    "event_type",
    "linked",
    "key",
    "le",
    "method",
    "route",
    "status",
    "code",
    "generation",
}


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def serve(**overrides: object) -> tuple[RecommendRequest, RecommendResponse]:
    fields: dict[str, object] = {"user_id": 7, "top_k": 20, "pantry": [], "allergies": []}
    fields.update(overrides)
    request = RecommendRequest(**fields)
    return request, mock.build_recommendation(request)


def requests_by(engine: str) -> float:
    return sum(
        value("reco_requests_total", engine=engine, user_mode=mode, degraded=degraded)
        for mode in ("onboarding", "blended", "behavior", "unknown")
        for degraded in ("true", "false", "unknown")
    )


def test_a_served_list_is_counted_by_engine_slot_and_signal() -> None:
    request, response = serve()
    before = {
        "requests": requests_by("mock"),
        "personal": value("reco_items_total", slot="personal"),
        "explored": value("reco_items_total", slot="exploration"),
        "lists": value("reco_list_length_count"),
    }

    monitor.observe_recommendation(request, response)

    assert requests_by("mock") - before["requests"] == 1
    explored = sum(1 for item in response.items if item.is_exploration)
    assert value("reco_items_total", slot="exploration") - before["explored"] == explored
    assert value("reco_items_total", slot="personal") - before["personal"] == 20 - explored
    assert value("reco_list_length_count") - before["lists"] == 1


def test_a_signal_without_a_value_is_counted_as_missing_not_as_zero() -> None:
    """None 과 0.0 은 뜻이 다릅니다. 0 으로 세면 꺼진 신호가 "값이 낮은 신호" 로 보입니다."""
    request, response = serve()
    feature = sorted(UNAVAILABLE_FEATURES)[0]
    missing = value("reco_feature_items_total", feature=feature, state="none")
    present = value("reco_feature_items_total", feature=feature, state="value")

    monitor.observe_recommendation(request, response)

    assert value("reco_feature_items_total", feature=feature, state="none") - missing == 20
    assert value("reco_feature_items_total", feature=feature, state="value") == present
    assert all(
        value("reco_feature_items_total", feature=key, state="none")
        + value("reco_feature_items_total", feature=key, state="value")
        > 0
        for key in FEATURE_KEYS
    )


def test_production_calls_without_a_trace_are_read_from_the_log() -> None:
    """운영 호출은 `include_trace=false` 입니다. 추적이 없다고 버리면 운영만 안 보입니다."""
    request, response = serve(include_trace=False)
    assert response.trace is None
    stages = value("reco_stage_latency_seconds_count", stage="rerank")
    unknown = value("reco_requests_total", engine="mock", user_mode="unknown", degraded="unknown")

    monitor.observe_recommendation(request, response, mock.read_log(response.request_id))

    assert value("reco_stage_latency_seconds_count", stage="rerank") - stages == 1
    assert (
        value("reco_requests_total", engine="mock", user_mode="unknown", degraded="unknown")
        == unknown
    )


def test_neither_a_trace_nor_a_log_still_counts_the_list() -> None:
    request, response = serve(include_trace=False)
    lists = value("reco_list_length_count")

    monitor.observe_recommendation(request, response, None)

    assert value("reco_list_length_count") - lists == 1


def test_the_real_engine_is_told_apart_from_the_mock_by_its_policy_id() -> None:
    """목업이 답하는 동안의 수치를 엔진의 것으로 읽으면 안 됩니다."""
    request, response = serve()
    assert response.trace is not None
    response.trace.stages[-1].params["policy_id"] = POLICY_ID
    real, mocked = requests_by("real"), requests_by("mock")

    monitor.observe_recommendation(request, response)

    assert requests_by("real") - real == 1
    assert requests_by("mock") == mocked


def test_an_unknown_allergy_label_is_counted_as_unprotected() -> None:
    request, response = serve(allergies=["우유", " 땅콩 알레르기", "아황산류", "  "])
    known = value("reco_allergy_labels_total", source="recommend", known="yes")
    unknown = value("reco_allergy_labels_total", source="recommend", known="no")

    monitor.observe_recommendation(request, response)

    assert value("reco_allergy_labels_total", source="recommend", known="yes") - known == 2
    assert value("reco_allergy_labels_total", source="recommend", known="no") - unknown == 1


def test_a_reason_the_engine_made_up_cannot_mint_a_new_series() -> None:
    """추적의 사유 키는 엔진이 정하는 문자열입니다. 모양이 어긋나면 `other` 로 뭉칩니다."""
    request, response = serve()
    assert response.trace is not None
    response.trace.stages[0].filters = {"user 1024 의 재료": 3, "allergy_cut": 2}
    other = value("reco_filtered_total", stage="retrieval", reason="other")
    cut = value("reco_filtered_total", stage="retrieval", reason="allergy_cut")

    monitor.observe_recommendation(request, response)

    assert value("reco_filtered_total", stage="retrieval", reason="other") - other == 3
    assert value("reco_filtered_total", stage="retrieval", reason="allergy_cut") - cut == 2


def test_an_event_is_tied_to_the_slot_it_came_from() -> None:
    _request, response = serve()
    log = mock.read_log(response.request_id)
    explored = next(item for item in response.items if item.is_exploration)
    personal = next(item for item in response.items if not item.is_exploration)
    batch = EventBatchIn(
        events=[
            EventIn(
                user_id=7,
                event_type=EventType.CLICK,
                recipe_id=explored.recipe_id,
                request_id=response.request_id,
                position=explored.final_rank,
            ),
            EventIn(
                user_id=7,
                event_type=EventType.CLICK,
                recipe_id=personal.recipe_id,
                request_id=response.request_id,
                position=1,
            ),
            EventIn(user_id=7, event_type=EventType.SEARCH),
        ]
    )
    before = {
        slot: value("reco_events_total", event_type="click", linked="yes", slot=slot)
        for slot in ("exploration", "personal")
    }
    unlinked = value("reco_events_total", event_type="search", linked="no", slot="unknown")
    rejected = value("reco_events_rejected_total")

    monitor.observe_events(
        batch,
        EventAck(accepted=2, rejected=1, errors=["search"]),
        lambda _request_id, recipe_id: monitor.slot_in_log(log, recipe_id),
    )

    for slot in ("exploration", "personal"):
        after = value("reco_events_total", event_type="click", linked="yes", slot=slot)
        assert after - before[slot] == 1
    assert (
        value("reco_events_total", event_type="search", linked="no", slot="unknown") - unlinked == 1
    )
    assert value("reco_events_rejected_total") - rejected == 1


def test_a_recipe_that_was_not_served_has_no_slot() -> None:
    _request, response = serve()
    log = mock.read_log(response.request_id)

    assert monitor.slot_in_log(log, -1) == monitor.SLOT_UNKNOWN
    assert monitor.slot_in_log(None, response.items[0].recipe_id) == monitor.SLOT_UNKNOWN
    assert monitor.slot_in_log(log, None) == monitor.SLOT_UNKNOWN


def test_onboarding_reports_the_labels_it_could_not_block() -> None:
    unmapped = value("reco_allergy_labels_total", source="onboarding", known="no")
    result = OnboardingOut(
        user_id=7,
        taste=TasteOut(spicy=0.5, salty=0.5, sweet=0.5),
        preferred_cuisines=[],
        allergy_groups=["dairy"],
        unmapped_allergens=["아황산류"],
        n_blocked_ingredients=9,
    )

    monitor.observe_onboarding(result)

    assert value("reco_allergy_labels_total", source="onboarding", known="no") - unmapped == 1


def test_swallowed_failures_become_a_metric() -> None:
    """DB 전환 M-07 — 서비스의 내부 카운터를 읽는 곳이 없었습니다."""
    collector = monitor.InternalCounters(
        lambda: {"failed": 3, "failed:OperationalError": 2, "OK?": 1}
    )

    samples = {s.labels["key"]: s.value for family in collector.collect() for s in family.samples}

    assert samples == {"failed": 3.0, "failed_operationalerror": 2.0, "other": 1.0}


def test_watching_twice_registers_once() -> None:
    """검사마다 앱을 새로 만듭니다. 그때마다 등록하면 같은 이름이 겹쳐 등록부가 예외를 냅니다."""
    monitor.watch_counters(lambda: {})
    monitor.watch_counters(lambda: {})


@pytest.mark.usefixtures("_env")
def test_no_metric_carries_an_identifier_as_a_label() -> None:
    """사용자 · 레시피 · 요청 id 가 라벨에 들어가면 시계열이 무한히 생기고 개인정보가 샙니다."""
    request, response = serve(user_id=987_654)
    monitor.observe_recommendation(request, response, mock.read_log(response.request_id))

    seen = {label for family in REGISTRY.collect() for s in family.samples for label in s.labels}
    text = " ".join(
        str(v) for family in REGISTRY.collect() for s in family.samples for v in s.labels.values()
    )

    assert seen <= ALLOWED_LABELS, seen - ALLOWED_LABELS
    assert "987654" not in text
    assert str(response.request_id) not in text
