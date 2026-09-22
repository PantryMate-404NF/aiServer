"""추천 엔진의 관측 — 서빙 한 건 한 건을 숫자로 남깁니다 (Prometheus).

엔진을 **계약으로만** 봅니다. 읽는 것은 요청(`RecommendRequest`) · 응답(`RecommendResponse`) · 로그
(`RecommendationLogOut`)뿐이라 목업이 답하든 실엔진이 답하든 같은 코드가 돕니다. 엔진 안에 관측
코드를 심지 않습니다 — 엔진이 평가 축을 부르면 축의 방향이 뒤집힙니다(02 의 2.2).

무엇을 남기는가는 "나중에 무엇을 물을 것인가" 에서 거꾸로 정했습니다.

    엔진이 답은 하고 있는가          → 요청 수 · 지연 · degraded · 목록 길이
    어느 신호가 꺼져 있는가          → 피처별 None 비율 (가중치가 있는데 값이 없는 신호)
    탐색은 설계대로 도는가           → 칸 종류별 건수 · 노출확률 · 부족분
    후보는 어디서 사라지는가         → 단계별 걸러짐 · 재정렬 탈락
    들어오는 입력은 온전한가         → 냉장고 크기 · 모르는 알레르기 라벨
    사용자는 반응하는가              → 이벤트 종류 · 순위 · 추천과 이어졌는가

주의: 라벨은 가짓수가 정해진 것만 씁니다(`utils/metrics.py`). 추적의 사유 키처럼 엔진이 정하는
   문자열은 `_label()` 을 지나며, 모양이 어긋나면 `other` 로 뭉칩니다.
주의: **관측은 서빙을 깨뜨리지 않습니다.** 호출하는 쪽(라우터)이 예외를 잡아 로그로 남깁니다.
   지표가 틀리는 것과 추천이 안 나가는 것은 무게가 다릅니다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Protocol

from prometheus_client import Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from features.recommend.engine import allergy
from features.recommend.enums import FEATURE_KEYS
from features.recommend.schema import (
    EventAck,
    EventBatchIn,
    OnboardingOut,
    RecommendationLogOut,
    RecommendRequest,
    RecommendResponse,
)
from features.recommend.stage import RankedItem, StageTrace
from utils.metrics import REGISTRY

#: 어느 엔진이 답했는가. 목업이 답하는 동안의 수치를 엔진의 것으로 읽으면 안 됩니다.
ENGINE_MOCK = "mock"
ENGINE_REAL = "real"
SLOT_PERSONAL = "personal"
SLOT_EXPLORATION = "exploration"
SLOT_CUISINE = "cuisine"
SLOT_UNKNOWN = "unknown"
_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_OTHER = "other"

REQUESTS = Counter(
    "reco_requests_total",
    "추천 응답 수",
    ["engine", "user_mode", "degraded"],
    registry=REGISTRY,
)
LATENCY = Histogram(
    "reco_latency_seconds",
    "엔진이 잰 추천 한 건의 처리 시간(초). HTTP 왕복은 http_request_duration_seconds 입니다",
    ["engine"],
    buckets=(0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.25, 0.5, 1.0, 3.0),
    registry=REGISTRY,
)
STAGE_LATENCY = Histogram(
    "reco_stage_latency_seconds",
    "단계별 처리 시간(초)",
    ["stage"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)
STAGE_OUT = Histogram(
    "reco_stage_out_count",
    "단계가 다음 단계로 넘긴 후보 수",
    ["stage"],
    buckets=(0, 5, 10, 20, 50, 100, 200, 500, 1000),
    registry=REGISTRY,
)
FILTERED = Counter(
    "reco_filtered_total", "단계에서 걸러진 후보 수", ["stage", "reason"], registry=REGISTRY
)
DROPPED = Counter(
    "reco_dropped_total", "재정렬에서 빠진 후보 수", ["stage", "reason"], registry=REGISTRY
)
LIST_LENGTH = Histogram(
    "reco_list_length",
    "서빙한 목록의 길이",
    buckets=(0, 1, 5, 10, 15, 19, 20, 50, 100),
    registry=REGISTRY,
)
ITEMS = Counter(
    "reco_items_total",
    "서빙한 아이템 수. 서버가 기록하는 노출(impression)과 같습니다",
    ["slot"],
    registry=REGISTRY,
)
ITEM_SCORE = Histogram(
    "reco_item_score",
    "서빙한 아이템의 점수",
    ["slot"],
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    registry=REGISTRY,
)
ITEM_MISSING = Histogram(
    "reco_item_missing_count",
    "서빙한 아이템의 부족 재료 수",
    buckets=(0, 1, 2, 3, 4),
    registry=REGISTRY,
)
PROPENSITY = Histogram(
    "reco_exploration_propensity",
    "탐색 칸의 노출확률. 0 에 붙으면 그 로그는 IPS 로 못 씁니다",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)
FEATURE_ITEMS = Counter(
    "reco_feature_items_total",
    "서빙한 아이템에서 그 신호가 값을 가졌는가(value) 없었는가(none)",
    ["feature", "state"],
    registry=REGISTRY,
)
FEATURE_VALUE_SUM = Counter(
    "reco_feature_value",
    "값이 있던 신호의 값 합계. 평균의 분자입니다",
    ["feature"],
    registry=REGISTRY,
)
PANTRY_SIZE = Histogram(
    "reco_request_pantry_size",
    "요청에 실려 온 냉장고의 재료 수",
    buckets=(0, 1, 3, 5, 10, 20, 50),
    registry=REGISTRY,
)
ALLERGY_LABELS = Counter(
    "reco_allergy_labels_total",
    "받은 알레르기 라벨. unknown 은 막지 못한 라벨입니다",
    ["source", "known"],
    registry=REGISTRY,
)
EVENTS = Counter(
    "reco_events_total",
    "받은 행동 이벤트. linked 는 추천(request_id)과 이어졌는가입니다",
    ["event_type", "linked", "slot"],
    registry=REGISTRY,
)
EVENTS_REJECTED = Counter("reco_events_rejected_total", "거부한 이벤트 수", registry=REGISTRY)
EVENT_POSITION = Histogram(
    "reco_event_position",
    "반응이 나온 순위. 순위별 반응 곡선의 재료입니다",
    ["event_type"],
    buckets=(1, 2, 3, 5, 10, 15, 20),
    registry=REGISTRY,
)


def observe_recommendation(
    request: RecommendRequest,
    response: RecommendResponse,
    log: RecommendationLogOut | None = None,
) -> None:
    """추천 한 건. `log` 가 있으면 추적을 거기서 읽습니다.

    운영 호출은 `include_trace=false` 라 응답에 추적이 없습니다. 추적은 로그에 그대로 남으므로
    로그를 함께 받습니다. 둘 다 없으면 단계 지표만 빠지고 나머지는 셉니다.
    """
    trace = response.trace if response.trace is not None else (log.stage_trace if log else None)
    engine = _engine_of(response, trace)
    user_mode = trace.totals.user_mode.value if trace else SLOT_UNKNOWN
    degraded = str(trace.totals.degraded).lower() if trace else SLOT_UNKNOWN
    REQUESTS.labels(engine, user_mode, degraded).inc()
    if trace is not None:
        LATENCY.labels(engine).observe(trace.totals.latency_ms / 1000)
        _observe_stages(trace)

    LIST_LENGTH.observe(len(response.items))
    for item in response.items:
        _observe_item(item)

    PANTRY_SIZE.observe(len(request.pantry))
    _observe_allergy_labels("recommend", request.allergies)


def observe_events(
    batch: EventBatchIn,
    ack: EventAck,
    slot_of: Callable[[object, int | None], str] | None = None,
) -> None:
    """이벤트 묶음. `slot_of(request_id, recipe_id)` 가 그 반응이 어느 칸에서 나왔는지 답합니다.

    칸을 알아야 "탐색 칸이 개인화 칸보다 반응이 좋은가" 를 물을 수 있습니다. 모르면 `unknown`.
    """
    EVENTS_REJECTED.inc(ack.rejected)
    for event in batch.events:
        linked = "yes" if event.request_id is not None else "no"
        slot = SLOT_UNKNOWN
        if slot_of is not None and event.request_id is not None:
            slot = slot_of(event.request_id, event.recipe_id)
        EVENTS.labels(event.event_type.value, linked, slot).inc()
        if event.position is not None:
            EVENT_POSITION.labels(event.event_type.value).observe(event.position)


def observe_onboarding(result: OnboardingOut) -> None:
    """온보딩 저장 한 건. 여기서 막지 못한 라벨이 나오면 그 사용자는 그만큼 보호받지 못합니다."""
    known = len(result.allergy_groups)
    if known:
        ALLERGY_LABELS.labels("onboarding", "yes").inc(known)
    if result.unmapped_allergens:
        ALLERGY_LABELS.labels("onboarding", "no").inc(len(result.unmapped_allergens))


def slot_in_log(log: RecommendationLogOut | None, recipe_id: int | None) -> str:
    """로그에서 그 레시피가 어느 칸이었는지. 서빙되지 않은 레시피면 `unknown` 입니다."""
    if log is None or recipe_id is None or recipe_id not in log.served:
        return SLOT_UNKNOWN
    explored = {rid for stage in log.stage_trace.stages for rid in stage.exploration_items}
    return SLOT_EXPLORATION if recipe_id in explored else SLOT_PERSONAL


class InternalCounters(Collector):
    """서비스가 세어 온 내부 카운터(`service.counters()`)를 수집 때마다 읽어 내보냅니다.

    로그 적재 실패 · 취향 저장 실패 같은 "삼킨 예외" 가 여기 모입니다. 읽는 곳이 없어서 DB 를
    붙인 뒤 적재가 전부 실패해도 API 는 200 이고 아무도 몰랐습니다(DB 전환 M-07).
    """

    def __init__(self, source: Callable[[], Mapping[str, int]]) -> None:
        self._source = source

    def collect(self) -> Iterable[CounterMetricFamily]:
        family = CounterMetricFamily(
            "reco_internal_events", "서비스 내부 카운터. 삼킨 예외와 조용한 분기", labels=["key"]
        )
        for key, value in sorted(self._source().items()):
            family.add_metric([_label(key.replace(":", "_").lower())], float(value))
        yield family


class CatalogState(Protocol):
    """실서빙이 알려 주는 사전의 상태(`serving.SyncState`). 모양만 보고 흐름은 부르지 않습니다."""

    ready: bool
    recipes: int
    synced_at: datetime | None


class CatalogSource(Protocol):
    def state(self) -> CatalogState: ...


class CatalogGauges(Collector):
    """레시피 사전이 있는가, 몇 건인가, 얼마나 묵었는가.

    실서빙에서 사전이 없으면 추천은 전부 503 입니다. 백엔드가 자기 인기순으로 대신하므로 화면은
    멀쩡해 보이고, 여기를 보지 않으면 추천이 한 건도 안 나가고 있다는 것을 모릅니다.
    """

    def __init__(self) -> None:
        self.source: CatalogSource | None = None

    def collect(self) -> Iterable[Metric]:
        live = GaugeMetricFamily("reco_serving_live", "실서빙이면 1, 목업이 답하면 0")
        ready = GaugeMetricFamily("reco_catalog_ready", "레시피 사전을 받았으면 1")
        recipes = GaugeMetricFamily("reco_catalog_recipes", "사전에 실린 레시피 수")
        age = GaugeMetricFamily("reco_catalog_age_seconds", "마지막 동기화로부터 지난 시간(초)")
        live.add_metric([], 0.0 if self.source is None else 1.0)
        if self.source is not None:
            state = self.source.state()
            ready.add_metric([], 1.0 if state.ready else 0.0)
            recipes.add_metric([], float(state.recipes))
            if state.synced_at is not None:
                age.add_metric([], (datetime.now(UTC) - state.synced_at).total_seconds())
        return [live, ready, recipes, age]


_watched: list[InternalCounters] = []
_catalog = CatalogGauges()
_catalog_registered: list[bool] = []


def watch_counters(source: Callable[[], Mapping[str, int]]) -> None:
    """내부 카운터를 등록합니다. 앱을 여러 번 만들어도(검사) 한 번만 등록됩니다."""
    if _watched:
        return
    collector = InternalCounters(source)
    REGISTRY.register(collector)
    _watched.append(collector)


def watch_catalog(source: CatalogSource | None) -> None:
    """사전의 상태를 지표로 냅니다. 앱을 다시 만들면 **새 실서빙을 가리키게** 바꿉니다."""
    _catalog.source = source
    if not _catalog_registered:
        REGISTRY.register(_catalog)
        _catalog_registered.append(True)


def _engine_of(response: RecommendResponse, trace: StageTrace | None) -> str:
    """목업인가. 추적의 `policy_id` 가 정본이고, 추적이 없으면 모델 이름으로 봅니다."""
    policy_id = ""
    if trace is not None and trace.stages:
        policy_id = str(trace.stages[-1].params.get("policy_id") or "")
    marker = policy_id or response.model_version
    return ENGINE_MOCK if marker.startswith("mock") else ENGINE_REAL


def _observe_stages(trace: StageTrace) -> None:
    for stage in trace.stages:
        name = stage.name.value
        STAGE_LATENCY.labels(name).observe(stage.latency_ms / 1000)
        STAGE_OUT.labels(name).observe(stage.out_count)
        for reason, count in stage.filters.items():
            FILTERED.labels(name, _label(reason)).inc(max(0, count))
        for reason, count in stage.dropped.items():
            DROPPED.labels(name, _label(reason)).inc(max(0, count))


def _observe_item(item: RankedItem) -> None:
    slot = _slot_of(item)
    ITEMS.labels(slot).inc()
    ITEM_SCORE.labels(slot).observe(item.score)
    ITEM_MISSING.observe(item.missing_count)
    if item.is_exploration and item.propensity is not None:
        PROPENSITY.observe(item.propensity)
    for feature in FEATURE_KEYS:
        value = item.features.get(feature)
        if value is None:
            FEATURE_ITEMS.labels(feature, "none").inc()
        else:
            FEATURE_ITEMS.labels(feature, "value").inc()
            FEATURE_VALUE_SUM.labels(feature).inc(max(0.0, float(value)))


def _slot_of(item: RankedItem) -> str:
    if item.is_exploration:
        return SLOT_EXPLORATION
    return SLOT_CUISINE if item.is_cuisine_slot else SLOT_PERSONAL


def _observe_allergy_labels(source: str, labels: Iterable[str]) -> None:
    given = [label for label in labels if allergy.normalize_label(label)]
    if not given:
        return
    unknown = len(allergy.resolve(given, {}, {}).unknown_labels)
    if len(given) - unknown:
        ALLERGY_LABELS.labels(source, "yes").inc(len(given) - unknown)
    if unknown:
        ALLERGY_LABELS.labels(source, "no").inc(unknown)


def _label(text: str) -> str:
    return text if _LABEL.match(text) else _OTHER
