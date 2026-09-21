"""쌓인 지표 → 할 일 (`evaluation/diagnosis.py`). 값을 직접 넣어 규칙 하나씩을 봅니다."""

from __future__ import annotations

import pytest

from features.recommend.enums import DEFAULT_WEIGHTS, FEATURE_KEYS
from features.recommend.evaluation import diagnosis
from utils.metrics import REGISTRY

Sample = tuple[str, dict[str, str], float]


def snap(*samples: Sample) -> dict[tuple[str, diagnosis.Labels], float]:
    return {(name, frozenset(labels.items())): value for name, labels, value in samples}


def requests(count: float, engine: str = "real", degraded: str = "false") -> Sample:
    labels = {"engine": engine, "user_mode": "onboarding", "degraded": degraded}
    return ("reco_requests_total", labels, count)


def feature(name: str, with_value: float, without: float) -> list[Sample]:
    return [
        ("reco_feature_items_total", {"feature": name, "state": "value"}, with_value),
        ("reco_feature_items_total", {"feature": name, "state": "none"}, without),
    ]


def titles(summary: diagnosis.MonitoringSummary) -> list[str]:
    return [finding.title for finding in summary.findings]


def test_nothing_served_yet_says_so_instead_of_reporting_zeroes() -> None:
    summary = diagnosis.summarize(snap())

    assert summary.engine == "none"
    assert summary.kpis["degraded_ratio"] is None
    assert titles(summary) == ["아직 추천 요청이 없습니다"]


def test_the_mock_is_named_so_its_numbers_are_not_read_as_the_engine() -> None:
    summary = diagnosis.summarize(snap(requests(40, engine="mock")))

    assert summary.engine == "mock"
    assert "추천을 목업이 답하고 있습니다" in titles(summary)
    assert diagnosis.summarize(snap(requests(40))).engine == "real"
    assert diagnosis.summarize(snap(requests(1, engine="mock"), requests(1))).engine == "mixed"


def test_a_weighted_signal_with_no_values_is_reported_with_its_weight() -> None:
    """가중치가 있는데 값이 없는 신호가 품질을 조용히 깎습니다. 에러는 나지 않습니다."""
    summary = diagnosis.summarize(
        snap(
            requests(100),
            *feature("f_popularity", 0, 2000),
            *feature("f_ing_pref", 0, 2000),
            *feature("f_coverage", 2000, 0),
            *feature("f_content", 0, 2000),
        )
    )

    dead = DEFAULT_WEIGHTS["f_popularity"] + DEFAULT_WEIGHTS["f_ing_pref"]
    assert summary.kpis["dead_weight"] == pytest.approx(dead)
    finding = next(f for f in summary.findings if "신호가 꺼져" in f.title)
    assert f"{dead:.2f}" in finding.title
    assert "f_popularity" in finding.evidence and "f_coverage" not in finding.evidence
    assert "스크랩 수" in finding.action
    # 가중치 0 인 신호는 꺼져 있어도 점수에 영향이 없어 세지 않습니다.
    assert DEFAULT_WEIGHTS["f_content"] == 0.0 and "f_content" not in finding.evidence


def test_every_signal_gets_a_row_even_before_traffic() -> None:
    summary = diagnosis.summarize(snap())

    assert [row.feature for row in summary.features] == list(FEATURE_KEYS)
    assert all(row.none_ratio is None and row.observed == 0 for row in summary.features)


def test_an_unblocked_allergy_label_outranks_everything() -> None:
    summary = diagnosis.summarize(
        snap(
            requests(100, engine="mock"),
            ("reco_allergy_labels_total", {"source": "recommend", "known": "no"}, 2),
            ("reco_dropped_total", {"stage": "rerank", "reason": "explore_shortfall"}, 5),
        )
    )

    assert summary.findings[0].severity == "critical"
    assert summary.findings[0].title == "막지 못한 알레르기 라벨이 들어왔습니다"
    assert [f.severity for f in summary.findings] == sorted(
        (f.severity for f in summary.findings), key=["critical", "warning", "info"].index
    )


def test_a_ratio_is_not_judged_on_a_handful_of_requests() -> None:
    """세 건 중 한 건은 33% 가 아니라 "아직 모른다" 입니다."""
    few = diagnosis.summarize(snap(requests(2), requests(1, degraded="true")))
    many = diagnosis.summarize(snap(requests(90), requests(10, degraded="true")))

    assert few.kpis["degraded_ratio"] == pytest.approx(1 / 3)
    assert "후보가 모자란 요청이 많습니다" not in titles(few)
    assert "후보가 모자란 요청이 많습니다" in titles(many)


def test_swallowed_failures_are_critical() -> None:
    summary = diagnosis.summarize(
        snap(
            requests(10),
            ("reco_internal_events_total", {"key": "failed"}, 4),
            ("reco_internal_events_total", {"key": "persona_onboarded"}, 9),
        )
    )

    finding = next(f for f in summary.findings if f.title == "삼킨 예외가 있습니다")
    assert finding.severity == "critical"
    assert "failed 4" in finding.evidence and "persona_onboarded" not in finding.evidence


def test_contract_violations_are_reported_with_their_codes() -> None:
    summary = diagnosis.summarize(
        snap(
            requests(100),
            (
                "http_requests_total",
                {"method": "POST", "route": "/v1/recommend", "status": "200"},
                95,
            ),
            (
                "http_requests_total",
                {"method": "POST", "route": "/v1/recommend", "status": "400"},
                5,
            ),
            ("http_validation_failures_total", {"route": "/v1/recommend", "code": "missing"}, 5),
        )
    )

    assert summary.kpis["validation_failure_ratio"] == pytest.approx(0.05)
    finding = next(f for f in summary.findings if "계약 위반" in f.title)
    assert "missing 5" in finding.evidence


def test_events_that_cannot_be_joined_are_reported() -> None:
    linked = {"event_type": "click", "linked": "yes", "slot": "personal"}
    unlinked = {"event_type": "cook", "linked": "no", "slot": "unknown"}
    summary = diagnosis.summarize(
        snap(requests(100), ("reco_events_total", linked, 60), ("reco_events_total", unlinked, 40))
    )

    assert summary.kpis["unlinked_event_ratio"] == pytest.approx(0.4)
    assert "추천과 이어지지 않는 이벤트가 많습니다" in titles(summary)


def test_exploration_beating_personalisation_is_a_signal_to_rederive_weights() -> None:
    click = {"event_type": "click", "linked": "yes"}
    summary = diagnosis.summarize(
        snap(
            requests(100),
            ("reco_items_total", {"slot": "personal"}, 1600),
            ("reco_items_total", {"slot": "exploration"}, 400),
            ("reco_events_total", {**click, "slot": "personal"}, 16),
            ("reco_events_total", {**click, "slot": "exploration"}, 12),
        )
    )

    assert summary.kpis["ctr_personal"] == pytest.approx(0.01)
    assert summary.kpis["ctr_exploration"] == pytest.approx(0.03)
    assert summary.kpis["exploration_share"] == pytest.approx(0.2)
    assert "탐색 칸이 개인화 칸보다 반응이 좋습니다" in titles(summary)


def test_the_quantile_interpolates_like_prometheus() -> None:
    name = "reco_latency_seconds"
    data = snap(
        (f"{name}_bucket", {"engine": "real", "le": "0.05"}, 50),
        (f"{name}_bucket", {"engine": "real", "le": "0.1"}, 90),
        (f"{name}_bucket", {"engine": "real", "le": "+Inf"}, 100),
        (f"{name}_count", {"engine": "real"}, 100),
    )

    assert diagnosis.quantile(data, name, 0.5) == pytest.approx(0.05)
    assert diagnosis.quantile(data, name, 0.7) == pytest.approx(0.075)
    # 마지막 유한 구간을 넘으면 그 경계를 돌려줍니다. 무한대를 그리지 않습니다.
    assert diagnosis.quantile(data, name, 0.99) == pytest.approx(0.1)
    assert diagnosis.quantile(snap(), name, 0.5) is None


def test_slow_answers_are_told_apart_from_answers_past_the_budget() -> None:
    def latency(name: str, labels: dict[str, str], edge: str) -> list[Sample]:
        return [
            (f"{name}_bucket", {**labels, "le": edge}, 100),
            (f"{name}_bucket", {**labels, "le": "+Inf"}, 100),
            (f"{name}_count", labels, 100),
        ]

    slow = diagnosis.summarize(
        snap(requests(100), *latency("reco_latency_seconds", {"engine": "real"}, "0.25"))
    )
    route = {"method": "POST", "route": "/v1/recommend"}
    late = diagnosis.summarize(
        snap(requests(100), *latency("http_request_duration_seconds", route, "5.0"))
    )

    assert "엔진 지연이 목표를 넘습니다" in titles(slow)
    assert next(f for f in late.findings if "시간 예산" in f.title).severity == "critical"


def test_the_live_registry_summarizes_into_a_serializable_report() -> None:
    summary = diagnosis.summarize(diagnosis.snapshot(REGISTRY))

    assert summary.model_dump(mode="json")["generated_at"]
    assert set(summary.kpis) >= {"requests", "dead_weight", "latency_p95", "ctr"}
