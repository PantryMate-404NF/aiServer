"""백엔드의 traceparent 에 ai-service 스팬이 이어지고, 프로브 경로는 스팬을 만들지 않는지."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from utils import tracing

# W3C traceparent: version-trace_id-parent_id-flags. 백엔드가 이 헤더를 넘깁니다.
UPSTREAM_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
UPSTREAM_SPAN_ID = "00f067aa0ba902b7"
TRACEPARENT = f"00-{UPSTREAM_TRACE_ID}-{UPSTREAM_SPAN_ID}-01"


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/ping")
    def ping() -> dict[str, str]:
        return {"pong": "ok"}

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_a_request_continues_the_upstream_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """컬렉터 대신 메모리 exporter 를 끼워 실제로 나가는 스팬을 봅니다."""
    monkeypatch.delenv("OTEL_SDK_DISABLED")
    exported = InMemorySpanExporter()
    monkeypatch.setattr(tracing, "OTLPSpanExporter", lambda **_: exported)
    # 전역 provider 는 프로세스당 한 번만 설정되므로(앞선 create_app 이 이미 했음) 잡아만 둡니다.
    installed: list[TracerProvider] = []
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", installed.append)
    app = _app()
    tracing.add_request_tracing(app)
    client = TestClient(app)

    assert client.get("/v1/ping", headers={"traceparent": TRACEPARENT}).status_code == 200
    assert client.get("/health/live").status_code == 200
    installed[0].force_flush()

    spans = exported.get_finished_spans()
    assert [s.name for s in spans] == ["GET /v1/ping"], "요청당 스팬 하나 · 프로브 경로는 없음"
    span = spans[0]
    assert format(span.context.trace_id, "032x") == UPSTREAM_TRACE_ID
    assert span.parent is not None and format(span.parent.span_id, "016x") == UPSTREAM_SPAN_ID
    assert span.resource.attributes["service.name"] == "ai-service"
