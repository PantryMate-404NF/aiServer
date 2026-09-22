"""Prometheus 지표의 등록부와 HTTP 공통 지표. 도메인 지식은 없습니다.

도메인 지표(추천 엔진의 점수·탐색·신호 결측)는 `features/recommend/evaluation/monitor.py` 가
이 등록부에 더합니다. 여기에는 어느 기능에나 같은 것만 둡니다 — 요청 수 · 지연 · 검증 실패.

주의: 라벨에 사용자 id · 레시피 id · 요청 id 를 넣지 않습니다. 값의 가짓수만큼 시계열이 생겨
   Prometheus 가 먼저 죽고, 그 값들은 개인정보이기도 합니다. 라벨은 가짓수가 정해진 것만 씁니다.
주의: 등록부는 프로세스 하나의 것입니다. uvicorn 워커를 둘 이상으로 늘리면 수집 때마다 다른
   워커가 답해 수치가 널뜁니다. 지금은 워커가 하나입니다(`Dockerfile`).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    GCCollector,
    Histogram,
    ProcessCollector,
    generate_latest,
)

#: 전역 기본 등록부를 쓰지 않습니다. 다른 라이브러리가 끼워 넣는 지표와 섞이지 않고, 검사에서
#: 값을 읽을 때 무엇이 들어 있는지 분명합니다.
REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

#: 요청 지표에서 빼는 경로. 수집기와 헬스체크가 몇 초마다 부르므로 넣으면 실제 트래픽이 묻힙니다.
UNOBSERVED_ROUTES = frozenset({"/metrics", "/health/live", "/health/ready"})
#: 어느 라우트에도 맞지 않은 요청. 경로를 그대로 라벨에 쓰면 스캐너가 시계열을 무한히 만듭니다.
UNMATCHED_ROUTE = "unmatched"

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "처리한 HTTP 요청 수",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP 요청 처리 시간(초)",
    ["method", "route"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
HTTP_VALIDATION_FAILURES = Counter(
    "http_validation_failures_total",
    "검증 실패(400)로 거부한 요청의 사유. 한 요청에 사유가 여럿이면 각각 셉니다",
    ["route", "code"],
    registry=REGISTRY,
)


def route_of(request: Request) -> str:
    """라벨에 쓸 경로. 값이 아니라 **틀**입니다 — `/v1/users/{user_id}/pantry`."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


def add_request_metrics(app: FastAPI) -> None:
    """요청마다 건수와 처리 시간을 셉니다. 예외로 끝난 요청은 500 으로 셉니다."""

    @app.middleware("http")
    async def _observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = route_of(request)
            if route not in UNOBSERVED_ROUTES:
                HTTP_REQUESTS.labels(request.method, route, str(status_code)).inc()
                HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - started)


def observe_validation_failure(request: Request, codes: Iterable[str]) -> None:
    """400 의 사유를 셉니다. 어느 필드였는지는 세지 않습니다 — 필드 이름은 보내는 쪽이 정합니다."""
    route = route_of(request)
    for code in codes:
        HTTP_VALIDATION_FAILURES.labels(route, code).inc()


def render() -> tuple[bytes, str]:
    """`/metrics` 의 본문과 Content-Type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
