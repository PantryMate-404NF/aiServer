"""쌓인 지표를 읽어 "지금 무엇을 고쳐야 하는가" 로 바꿉니다.

`monitor.py` 가 쓰는 쪽이고 여기가 읽는 쪽입니다. 등록부를 한 번 훑어 낱값으로 만들고(`snapshot`),
그것으로 요약 지표와 신호별 상태를 계산한 뒤(`summarize`), 규칙으로 할 일을 뽑습니다.

규칙은 전부 "에러 없이 틀리는 것" 을 겨눕니다. 추천 서버는 설계상 실패하지 않습니다 — 후보가
모자라면 인기순으로 채우고, 신호가 없으면 그 신호를 빼고 계산합니다. 그래서 상태코드와 에러
로그만 봐서는 품질이 무너져도 모릅니다. 여기의 규칙이 그 자리를 봅니다.

주의: 여기의 수치는 **이 프로세스가 뜬 뒤**의 누적입니다. 재시작하면 0 부터 다시 셉니다. 긴 기간의
   추이는 Prometheus 와 Grafana 가 봅니다. 관리자 페이지는 "지금 상태" 를 보는 곳입니다.
주의: 임계값은 전부 착수 추정치입니다 (예시값, 실제 데이터로 대체 필요).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Literal

from prometheus_client import CollectorRegistry
from pydantic import BaseModel, ConfigDict

from features.recommend.enums import (
    DEFAULT_WEIGHTS,
    FEATURE_KEYS,
    PENDING_DATA_FEATURES,
    UNAVAILABLE_FEATURES,
)

Labels = frozenset[tuple[str, str]]
Snapshot = Mapping[tuple[str, Labels], float]

#: 이보다 적으면 비율을 판정하지 않습니다. 세 건 중 한 건은 33% 가 아니라 "아직 모른다" 입니다.
MIN_REQUESTS = 30
MIN_IMPRESSIONS = 200
#: 서빙한 아이템 가운데 이만큼이 None 이면 그 신호는 꺼져 있는 것으로 봅니다.
DEAD_FEATURE_RATIO = 0.95
DEGRADED_RATIO_LIMIT = 0.05
VALIDATION_FAILURE_LIMIT = 0.01
UNLINKED_EVENT_LIMIT = 0.10
#: ②③ 의 지연 목표(설계 5-4)와 추천 응답의 시간 예산(API 명세 6절).
LATENCY_P95_TARGET = 0.058
LATENCY_BUDGET = 3.0
#: 목록이 같은 요리의 판본으로 이만큼 걸러지면 원천 데이터의 중복을 의심합니다.
SAME_DISH_PER_REQUEST_LIMIT = 100.0
#: 레시피 사전이 이보다 오래됐으면 동기화가 멈춘 것입니다. 하루 한 번 받으므로 이틀입니다.
CATALOG_STALE_SECONDS = 172_800

#: 꺼진 신호를 켜려면 무엇이 와야 하는가.
FEATURE_NEEDS: Mapping[str, str] = {
    "f_popularity": "레시피 동기화에 스크랩 수(popularity.scrap_count)를 받아 점수로 만듭니다",
    "f_quality": "평점 데이터(rating)가 와야 합니다",
    "f_ing_pref": "행동 이벤트가 쌓이고 사용자 이력 적재(DB 전환 M-03)가 연결돼야 합니다",
    "f_cooccur": "조리 이벤트가 쌓이고 사용자 이력 적재(DB 전환 M-03)가 연결돼야 합니다",
    "f_taste": "사용자가 온보딩에서 음식을 골라야 켜집니다. 온보딩 전에는 꺼진 것이 정상입니다",
    "f_expiring": "요청의 pantry 에 purchased_at 또는 expires_at 이 실려 와야 합니다",
    "f_cuisine": "사용자의 preferred_cuisines 와 레시피의 cuisine_type 이 둘 다 있어야 합니다",
    "f_season": "제철 시드가 없습니다 (enums.PENDING_DATA_FEATURES)",
    "f_dish_type": "레시피 분류축이 없습니다 (enums.PENDING_DATA_FEATURES)",
}


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Finding(_Out):
    """할 일 하나. `evidence` 는 그 판단의 숫자이고 `action` 은 다음에 할 일입니다."""

    severity: Literal["critical", "warning", "info"]
    title: str
    evidence: str
    action: str


class FeatureHealth(_Out):
    feature: str
    weight: float
    observed: int
    none_ratio: float | None
    mean: float | None
    note: str = ""


class MonitoringSummary(_Out):
    generated_at: datetime
    engine: Literal["mock", "real", "mixed", "none"]
    kpis: dict[str, float | None]
    features: list[FeatureHealth]
    slots: dict[str, float]
    events: dict[str, float]
    validation_codes: dict[str, float]
    internal: dict[str, float]
    findings: list[Finding]


def snapshot(registry: CollectorRegistry) -> dict[tuple[str, Labels], float]:
    """등록부의 모든 표본을 (이름, 라벨) → 값으로."""
    return {
        (sample.name, frozenset(sample.labels.items())): float(sample.value)
        for family in registry.collect()
        for sample in family.samples
    }


def total(data: Snapshot, name: str, **match: str) -> float:
    """이름이 같고 `match` 의 라벨을 가진 표본의 합."""
    wanted = set(match.items())
    return sum(
        value for (sample, labels), value in data.items() if sample == name and wanted <= labels
    )


def by_label(data: Snapshot, name: str, label: str, **match: str) -> dict[str, float]:
    """`label` 의 값마다 합을 냅니다."""
    wanted = set(match.items())
    out: dict[str, float] = {}
    for (sample, labels), value in data.items():
        if sample != name or not wanted <= labels:
            continue
        key = dict(labels).get(label)
        if key is not None:
            out[key] = out.get(key, 0.0) + value
    return out


def quantile(data: Snapshot, name: str, q: float, **match: str) -> float | None:
    """히스토그램의 분위수. Prometheus 의 `histogram_quantile` 과 같은 선형 보간입니다."""
    buckets = sorted(
        (float(edge), count)
        for edge, count in by_label(data, f"{name}_bucket", "le", **match).items()
        if edge != "+Inf"
    )
    count = total(data, f"{name}_count", **match)
    if count <= 0 or not buckets:
        return None
    rank = q * count
    lower_edge, lower_count = 0.0, 0.0
    for edge, cumulative in buckets:
        if cumulative >= rank:
            width = cumulative - lower_count
            return (
                edge
                if width <= 0
                else lower_edge + (edge - lower_edge) * (rank - lower_count) / width
            )
        lower_edge, lower_count = edge, cumulative
    return buckets[-1][0]


def summarize(data: Snapshot, now: datetime | None = None) -> MonitoringSummary:
    """지표 한 벌 → 요약. 순수 함수라 검사에서 값을 직접 넣어 봅니다."""
    requests = total(data, "reco_requests_total")
    engines = by_label(data, "reco_requests_total", "engine")
    items = by_label(data, "reco_items_total", "slot")
    impressions = sum(items.values())
    events = by_label(data, "reco_events_total", "event_type")
    http_recommend = total(data, "http_requests_total", route="/v1/recommend")
    rejected = total(data, "http_requests_total", route="/v1/recommend", status="400")
    unlinked = total(data, "reco_events_total", linked="no")
    event_count = sum(events.values())

    kpis: dict[str, float | None] = {
        "requests": requests,
        "mock_share": _ratio(engines.get("mock", 0.0), requests),
        "degraded_ratio": _ratio(total(data, "reco_requests_total", degraded="true"), requests),
        "latency_p50": quantile(data, "reco_latency_seconds", 0.5),
        "latency_p95": quantile(data, "reco_latency_seconds", 0.95),
        "http_latency_p95": quantile(
            data, "http_request_duration_seconds", 0.95, route="/v1/recommend"
        ),
        "list_length_mean": _ratio(total(data, "reco_list_length_sum"), requests),
        "impressions": impressions,
        "exploration_share": _ratio(items.get("exploration", 0.0), impressions),
        "validation_failure_ratio": _ratio(rejected, http_recommend),
        "same_dish_per_request": _ratio(
            total(data, "reco_dropped_total", reason="same_dish"), requests
        ),
        "explore_shortfall": total(data, "reco_dropped_total", reason="explore_shortfall"),
        "unknown_allergy_labels": total(data, "reco_allergy_labels_total", known="no"),
        "empty_pantry_ratio": _ratio(
            total(data, "reco_request_pantry_size_bucket", le="0.0"), requests
        ),
        "events": event_count,
        "unlinked_event_ratio": _ratio(unlinked, event_count),
        "ctr": _ratio(events.get("click", 0.0), impressions),
        "save_rate": _ratio(events.get("save", 0.0), impressions),
        "cook_rate": _ratio(events.get("cook", 0.0), impressions),
        "ctr_personal": _ratio(
            total(data, "reco_events_total", event_type="click", slot="personal"),
            items.get("personal", 0.0) + items.get("cuisine", 0.0),
        ),
        "ctr_exploration": _ratio(
            total(data, "reco_events_total", event_type="click", slot="exploration"),
            items.get("exploration", 0.0),
        ),
        "serving_live": total(data, "reco_serving_live"),
        "catalog_ready": _gauge(data, "reco_catalog_ready"),
        "catalog_recipes": _gauge(data, "reco_catalog_recipes"),
        "catalog_age_seconds": _gauge(data, "reco_catalog_age_seconds"),
    }
    features = [_feature_health(data, feature) for feature in FEATURE_KEYS]
    kpis["dead_weight"] = round(
        sum(row.weight for row in features if _is_dead(row) and row.weight > 0.0), 4
    )
    summary = MonitoringSummary(
        generated_at=now or datetime.now(UTC),
        engine=_engine(engines),
        kpis=kpis,
        features=features,
        slots=items,
        events=events,
        # 추천 경로의 사유만 셉니다. 온보딩의 400(제시 목록에 없는 음식)이 섞이면 추천 계약이
        # 어긋난 것처럼 읽힙니다 — 2026-09-22 실트래픽에서 그렇게 보였습니다.
        validation_codes=by_label(
            data, "http_validation_failures_total", "code", route="/v1/recommend"
        ),
        internal=by_label(data, "reco_internal_events_total", "key"),
        findings=[],
    )
    return summary.model_copy(update={"findings": diagnose(summary)})


def diagnose(summary: MonitoringSummary) -> list[Finding]:
    """요약 → 할 일. 심각한 것부터 냅니다."""
    found = [finding for rule in _RULES for finding in rule(summary)]
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda finding: order[finding.severity])


def _rule_mock(s: MonitoringSummary) -> Iterable[Finding]:
    if s.engine in ("mock", "mixed"):
        share = s.kpis.get("mock_share") or 0.0
        yield Finding(
            severity="warning",
            title="추천을 목업이 답하고 있습니다",
            evidence=f"응답의 {share:.0%} 가 목업입니다",
            action="아래 품질 수치는 엔진의 것이 아닙니다. "
            "라우터 실연결(DB 전환 M-01) 뒤에 다시 봅니다",
        )


def _rule_no_traffic(s: MonitoringSummary) -> Iterable[Finding]:
    if not s.kpis["requests"]:
        yield Finding(
            severity="info",
            title="아직 추천 요청이 없습니다",
            evidence="reco_requests_total = 0",
            action="서버가 뜬 뒤 받은 요청이 없습니다. 재시작 직후라면 정상입니다",
        )


def _rule_catalog(s: MonitoringSummary) -> Iterable[Finding]:
    if not s.kpis["serving_live"]:
        return
    age = s.kpis["catalog_age_seconds"]
    if not s.kpis["catalog_ready"]:
        yield Finding(
            severity="critical",
            title="레시피 사전을 받지 못해 추천이 전부 503 입니다",
            evidence="reco_catalog_ready = 0. "
            "백엔드가 자기 인기순으로 대신하고 있어 화면은 멀쩡해 보입니다",
            action="서버 로그의 'catalog sync failed' 에서 원인을 봅니다. "
            "BACKEND_BASE_URL · 경로 · "
            "방화벽 · 내부 키가 맞는지, 백엔드의 두 API 가 떠 있는지 확인합니다",
        )
    elif age is not None and age > CATALOG_STALE_SECONDS:
        yield Finding(
            severity="warning",
            title="레시피 사전이 오래됐습니다",
            evidence=f"마지막 동기화로부터 {age / 3600:.0f}시간 "
            f"(기준 {CATALOG_STALE_SECONDS / 3600:.0f}시간)",
            action="동기화가 계속 실패하고 있습니다. 어제의 사전으로 서빙 중이라 새 레시피와 "
            "지워진 레시피가 반영되지 않습니다. 서버 로그의 'catalog sync failed' 를 봅니다",
        )


def _rule_unknown_allergy(s: MonitoringSummary) -> Iterable[Finding]:
    unknown = s.kpis["unknown_allergy_labels"] or 0.0
    if unknown > 0:
        yield Finding(
            severity="critical",
            title="막지 못한 알레르기 라벨이 들어왔습니다",
            evidence=f"모르는 라벨 {unknown:.0f}건. "
            "그 사용자는 그만큼 보호받지 못했고 응답은 200 이었습니다",
            action="서버 로그의 'unknown allergy labels' 에서 라벨을 찾아 "
            "enums.ALLERGEN_LABELS 또는 "
            "engine/allergy.py 의 동의어에 더합니다",
        )


def _rule_internal_failures(s: MonitoringSummary) -> Iterable[Finding]:
    failed = {key: value for key, value in s.internal.items() if "fail" in key or "error" in key}
    if sum(failed.values()) > 0:
        worst = ", ".join(f"{key} {value:.0f}" for key, value in sorted(failed.items()) if value)
        yield Finding(
            severity="critical",
            title="삼킨 예외가 있습니다",
            evidence=worst,
            action="로그 적재나 취향 저장이 실패했는데 API 는 200 을 냈습니다. "
            "서버 로그에서 원인을 봅니다",
        )


def _rule_dead_features(s: MonitoringSummary) -> Iterable[Finding]:
    dead = [row for row in s.features if _is_dead(row) and row.weight > 0.0]
    if not dead:
        return
    weight = sum(row.weight for row in dead)
    names = ", ".join(f"{row.feature}({row.weight:.2f})" for row in dead)
    needs = " / ".join(
        f"{row.feature}: {FEATURE_NEEDS.get(row.feature, '원천 데이터를 확인합니다')}"
        for row in dead
    )
    yield Finding(
        severity="warning",
        title=f"가중치 {weight:.2f} 만큼의 신호가 꺼져 있습니다",
        evidence=f"서빙한 아이템의 {DEAD_FEATURE_RATIO:.0%} 이상에서 값이 없습니다 — {names}",
        action=needs,
    )


def _rule_degraded(s: MonitoringSummary) -> Iterable[Finding]:
    ratio = s.kpis["degraded_ratio"]
    if _enough(s) and ratio is not None and ratio > DEGRADED_RATIO_LIMIT:
        yield Finding(
            severity="warning",
            title="후보가 모자란 요청이 많습니다",
            evidence=f"degraded {ratio:.1%} (기준 {DEGRADED_RATIO_LIMIT:.0%})",
            action="목록이 요청한 길이를 못 채웠습니다. "
            "후보 완화 단계(policy.max_missing_relaxed)와 "
            "재료 사전이 냉장고 재료를 덮는지 봅니다",
        )


def _rule_explore_shortfall(s: MonitoringSummary) -> Iterable[Finding]:
    shortfall = s.kpis["explore_shortfall"] or 0.0
    if shortfall > 0:
        yield Finding(
            severity="warning",
            title="탐색 칸을 다 채우지 못했습니다",
            evidence=f"부족분 누적 {shortfall:.0f}칸",
            action="탐색 풀이 작습니다. ① 조회가 덜 가져왔거나 판본 묶기로 후보가 줄었습니다. "
            "탐색이 줄면 새 취향을 못 찾고 IPS 표본도 줍니다",
        )


def _rule_latency(s: MonitoringSummary) -> Iterable[Finding]:
    engine_p95, http_p95 = s.kpis["latency_p95"], s.kpis["http_latency_p95"]
    if http_p95 is not None and http_p95 > LATENCY_BUDGET:
        yield Finding(
            severity="critical",
            title="추천 응답이 시간 예산을 넘습니다",
            evidence=f"HTTP p95 {http_p95:.2f}s (예산 {LATENCY_BUDGET:.0f}s)",
            action="백엔드는 그 전에 끊고 인기순으로 대신합니다. "
            "단계별 지연(reco_stage_latency_seconds)을 봅니다",
        )
    elif _enough(s) and engine_p95 is not None and engine_p95 > LATENCY_P95_TARGET:
        yield Finding(
            severity="info",
            title="엔진 지연이 목표를 넘습니다",
            evidence=f"엔진 p95 {engine_p95 * 1000:.0f}ms (목표 {LATENCY_P95_TARGET * 1000:.0f}ms)",
            action="단계별 지연에서 어느 단계인지 봅니다. "
            "요리 이름은 동기화 때 미리 뽑아 둘 수 있습니다",
        )


def _rule_validation(s: MonitoringSummary) -> Iterable[Finding]:
    ratio = s.kpis["validation_failure_ratio"]
    if _enough(s) and ratio is not None and ratio > VALIDATION_FAILURE_LIMIT:
        codes = ", ".join(
            f"{code} {count:.0f}" for code, count in sorted(s.validation_codes.items())
        )
        yield Finding(
            severity="warning",
            title="추천 요청이 계약 위반으로 거부되고 있습니다",
            evidence=f"/v1/recommend 의 {ratio:.1%} 가 400 — {codes}",
            action="missing 이면 백엔드가 pantry · allergies 를 빼고 보낸 것이고, "
            "extra_forbidden 이면 "
            "명세에 없는 필드를 보낸 것입니다. 백엔드와 명세(docs/backend_api_spec.md)를 맞춥니다",
        )


def _rule_unlinked_events(s: MonitoringSummary) -> Iterable[Finding]:
    ratio = s.kpis["unlinked_event_ratio"]
    if (
        (s.kpis["events"] or 0.0) >= MIN_REQUESTS
        and ratio is not None
        and ratio > UNLINKED_EVENT_LIMIT
    ):
        yield Finding(
            severity="warning",
            title="추천과 이어지지 않는 이벤트가 많습니다",
            evidence=f"request_id 없는 이벤트 {ratio:.1%} (기준 {UNLINKED_EVENT_LIMIT:.0%})",
            action="그 반응은 어느 추천의 결과로도 셀 수 없습니다. "
            "백엔드가 추천 응답의 request_id 를 "
            "이벤트에 싣는지 확인합니다",
        )


def _rule_exploration_beats_personal(s: MonitoringSummary) -> Iterable[Finding]:
    personal, explored = s.kpis["ctr_personal"], s.kpis["ctr_exploration"]
    enough = (s.kpis["impressions"] or 0.0) >= MIN_IMPRESSIONS
    if enough and personal is not None and explored is not None and explored > personal > 0.0:
        yield Finding(
            severity="warning",
            title="탐색 칸이 개인화 칸보다 반응이 좋습니다",
            evidence=f"클릭률 탐색 {explored:.2%} 대 개인화 {personal:.2%}",
            action="무작위에 가까운 칸이 점수로 고른 칸을 이깁니다. "
            "가중치를 다시 유도합니다(T-14 쌍대비교)",
        )


def _rule_same_dish(s: MonitoringSummary) -> Iterable[Finding]:
    per_request = s.kpis["same_dish_per_request"]
    if _enough(s) and per_request is not None and per_request > SAME_DISH_PER_REQUEST_LIMIT:
        yield Finding(
            severity="info",
            title="후보의 많은 몫이 같은 요리의 판본입니다",
            evidence=f"요청당 {per_request:.0f}건이 판본으로 걸러집니다",
            action="원천 데이터의 중복입니다. "
            "백엔드에 대표 번호가 생기면 그쪽에서 묶는 편이 낫습니다",
        )


_RULES = (
    _rule_catalog,
    _rule_no_traffic,
    _rule_mock,
    _rule_unknown_allergy,
    _rule_internal_failures,
    _rule_dead_features,
    _rule_degraded,
    _rule_explore_shortfall,
    _rule_latency,
    _rule_validation,
    _rule_unlinked_events,
    _rule_exploration_beats_personal,
    _rule_same_dish,
)


def _feature_health(data: Snapshot, feature: str) -> FeatureHealth:
    with_value = total(data, "reco_feature_items_total", feature=feature, state="value")
    without = total(data, "reco_feature_items_total", feature=feature, state="none")
    observed = with_value + without
    note = ""
    if feature in UNAVAILABLE_FEATURES:
        note = "수단이 아직 없습니다"
    elif feature in PENDING_DATA_FEATURES:
        note = "데이터가 오면 켜집니다"
    return FeatureHealth(
        feature=feature,
        weight=DEFAULT_WEIGHTS.get(feature, 0.0),
        observed=int(observed),
        none_ratio=_ratio(without, observed),
        mean=_ratio(total(data, "reco_feature_value_total", feature=feature), with_value),
        note=note,
    )


def _is_dead(row: FeatureHealth) -> bool:
    return row.observed > 0 and row.none_ratio is not None and row.none_ratio >= DEAD_FEATURE_RATIO


def _engine(engines: Mapping[str, float]) -> Literal["mock", "real", "mixed", "none"]:
    mock, real = engines.get("mock", 0.0) > 0, engines.get("real", 0.0) > 0
    if mock and real:
        return "mixed"
    if mock:
        return "mock"
    return "real" if real else "none"


def _enough(summary: MonitoringSummary) -> bool:
    return (summary.kpis["requests"] or 0.0) >= MIN_REQUESTS


def _gauge(data: Snapshot, name: str) -> float | None:
    """게이지 하나. 표본이 없으면 None 입니다 — 0 과 "모른다" 는 다릅니다."""
    values = [value for (sample, _labels), value in data.items() if sample == name]
    return values[0] if values else None


def _ratio(numerator: float, denominator: float) -> float | None:
    return None if denominator <= 0 else numerator / denominator
