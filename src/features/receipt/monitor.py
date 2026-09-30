"""영수증 처리의 관측 — 한 장 한 장을 숫자로 남깁니다 (Prometheus).

로그에만 있던 값을 지표로 냅니다. 런북 8.1 의 영수증 경보는 전부 이 숫자를 봅니다.

    실패는 어느 코드로 나는가        → 결과별 건수 (OCR_EMPTY 비율, 5xx 비율)
    어느 단계가 느린가               → 단계별 처리 시간. HTTP 지표의 버킷은 10초까지라
                                        영수증(8~20초)을 못 잽니다. 여기 버킷은 30초까지입니다
    인식 품질이 흘러내리는가         → 평균 인식 점수 · 검출 조각 수 · 식재료 수 · 제외 수
                                        (이미지를 저장하지 않으므로 출력값 분포로 드리프트를 봅니다)

주의: 라벨은 가짓수가 정해진 것만 씁니다(`utils/metrics.py`). receipt_id 는 넣지 않습니다.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

from utils.metrics import REGISTRY

OUTCOME_OK = "ok"

OUTCOMES = Counter(
    "receipt_requests_total",
    "영수증 처리 결과 수. code 는 성공이면 ok, 실패면 응답의 error.code",
    ["code"],
    registry=REGISTRY,
)
STAGE_SECONDS = Histogram(
    "receipt_stage_seconds",
    "단계별 처리 시간(초). total 은 자리 대기부터 응답 조립까지입니다",
    ["stage"],
    buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0),
    registry=REGISTRY,
)
CONFIDENCE = Histogram(
    "receipt_confidence",
    "영수증 단위 평균 인식 점수(0~1)",
    buckets=(0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0),
    registry=REGISTRY,
)
CELLS = Histogram(
    "receipt_cells",
    "OCR 이 검출한 텍스트 조각 수",
    buckets=(0, 5, 10, 20, 40, 80, 160, 320),
    registry=REGISTRY,
)
ITEMS = Histogram(
    "receipt_items",
    "응답에 실은 식재료 수",
    buckets=(0, 1, 2, 3, 5, 8, 12, 20, 40),
    registry=REGISTRY,
)
DROPPED = Histogram(
    "receipt_dropped_items",
    "비식재료로 판정해 응답에서 뺀 품목 수",
    buckets=(0, 1, 2, 3, 5, 8, 12, 20),
    registry=REGISTRY,
)


def observe_success(
    *,
    ocr_s: float,
    llm_s: float,
    total_s: float,
    confidence: float,
    cells: int,
    items: int,
    dropped: int,
) -> None:
    OUTCOMES.labels(OUTCOME_OK).inc()
    STAGE_SECONDS.labels("ocr").observe(ocr_s)
    STAGE_SECONDS.labels("llm").observe(llm_s)
    STAGE_SECONDS.labels("total").observe(total_s)
    CONFIDENCE.observe(confidence)
    CELLS.observe(cells)
    ITEMS.observe(items)
    DROPPED.observe(dropped)


def observe_failure(code: str, total_s: float) -> None:
    """실패도 시간을 셉니다. 대기 상한에 걸린 실패는 그 시간만큼 느린 실패입니다."""
    OUTCOMES.labels(code).inc()
    STAGE_SECONDS.labels("total").observe(total_s)
