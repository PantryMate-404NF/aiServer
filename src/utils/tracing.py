"""분산 추적. 게이트웨이 → 백엔드 → 이 서버까지를 한 트레이스로 잇습니다.

백엔드가 보내는 `traceparent` 헤더를 FastAPI 계측이 받아 이어 붙이고, 스팬은 OTLP/gRPC 로
컬렉터에 보냅니다. `prometheus_client` 지표와는 별개 라이브러리라 `/metrics` 와 충돌하지 않습니다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from utils.metrics import UNOBSERVED_ROUTES

logger = logging.getLogger(__name__)

SERVICE_NAME = "ai-service"
OTEL_COLLECTOR_ENDPOINT = "otel-collector.monitoring.svc.cluster.local:4317"


def add_request_tracing(app: FastAPI) -> None:
    """요청마다 서버 스팬을 만들어 컬렉터로 보냅니다.

    스크래퍼와 프로브가 치는 경로는 지표에서 빼는 것과 같은 이유로 트레이스에서도 뺍니다.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))

    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_COLLECTOR_ENDPOINT, insecure=True))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=",".join(UNOBSERVED_ROUTES),
        exclude_spans=["receive", "send"],
    )
    logger.info("tracing enabled (service=%s · %s)", SERVICE_NAME, OTEL_COLLECTOR_ENDPOINT)
