"""애플리케이션 진입점. `uv run uvicorn main:create_app --factory` 로 실행합니다.

    uvicorn main:create_app --factory --reload --port 8000
    http://localhost:8000/docs        ← OpenAPI 문서 자동 생성

**목적: 대시보드 트랙이 엔진 완성을 기다리지 않게 하는 것입니다.**
엔진과 대시보드를 순차로 진행하면 남은 기간이 모자랍니다.
두 트랙을 동시에 진행하려면 이 서버가 먼저 있어야 합니다.

지금 응답은 `features/recommend/engine/mock.py` 가 만듭니다 — 고정 시드라
재현 가능하고 실제 추천 로직은 없습니다. 각 스테이지 담당자가 자기 mock 을
실제 구현으로 갈아끼우면 라우터와 계약은 그대로 둔 채 서비스만 바뀝니다.

02 의 3.1 — 여기는 **앱과 라우터 등록만** 합니다.

⬜ 01 의 7.1 은 LLM 클라이언트·임베딩 모델을 `lifespan` 에서 1회 만들라고 합니다.
   지금은 둘 다 없어서 lifespan 이 비어 있습니다 — 생기는 시점에 여기 붙입니다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from config import get_settings
from deps import verify_internal_api_key, verify_scraper_key
from features import receipt
from features.receipt.router import router as receipt_router
from features.receipt.schema import ReceiptErrorDetail, ReceiptErrorResponse
from features.recommend import service as recommend_service
from features.recommend import serving
from features.recommend.enums import CONTRACT_VERSION
from features.recommend.evaluation import monitor
from features.recommend.evaluation.router import page_router as monitoring_page_router
from features.recommend.evaluation.router import router as monitoring_router
from features.recommend.router import router as recommend_router
from features.recommend.schema import validation_error_body
from infra import gemini
from utils.errors import ReceiptError
from utils.logging import add_request_logging, configure_logging
from utils.metrics import add_request_metrics, observe_validation_failure, render

logger = logging.getLogger(__name__)

RECEIPT_FAILURE_STATUS = 500
NOT_READY_STATUS = 503


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """무거운 자원을 여기서 한 번 올리고 종료 때 내립니다.

    모델 로딩을 기다리지 않고 넘어갑니다. 그래야 프로세스가 바로 떠서 `/health/live` 가
    답하고, 로딩이 끝나기 전까지는 `/health/ready` 가 503 으로 트래픽을 막습니다.
    """
    # LLM 클라이언트 생성은 네트워크를 타지 않아 즉시 끝납니다. 여기서 만들어 두면
    # 첫 요청이 클라이언트 조립 비용을 물지 않습니다.
    gemini.get_client()
    warmup = asyncio.create_task(receipt.start_pool())
    warmup.add_done_callback(_log_warmup_result)
    # 레시피 사전은 뒤에서 받습니다. 기다리지 않습니다 — 백엔드가 늦게 떠도 앱은 떠야 하고,
    # 그동안 추천은 503 으로 답해 백엔드가 자기 인기순으로 대신하게 합니다.
    live = getattr(app.state, "live_serving", None)
    if isinstance(live, serving.LiveServing):
        live.start()
    try:
        yield
    finally:
        warmup.cancel()
        receipt.shutdown_pool()
        if isinstance(live, serving.LiveServing):
            live.stop()


async def handle_missing_field(request: Request, exc: Exception) -> Response:
    """필드가 빠진 요청은 400 입니다. FastAPI 기본값인 422 는 계약에 없습니다.

    영수증 경로는 본문을 비웁니다. 이 경계를 넘는 요청은 백엔드가 보낸 것이라 사용자에게
    보일 문구가 없고, 어느 필드가 빠졌는지는 로그에만 남깁니다.

    그 밖의 경로는 어느 필드가 왜 걸렸는지를 본문에 싣습니다(2026-09-21 백엔드 요청).
    추천 계약은 모르는 필드 하나로 요청 전체를 거부하므로, 본문이 비어 있으면 백엔드가
    원인을 알 길이 없습니다. 보낸 값을 통째로 되돌려주지는 않습니다(`validation_error_body`).
    """
    logger.warning("request is missing required fields: %s", exc)
    errors = cast(RequestValidationError, exc).errors()
    # 본문이 빈 영수증 경로도 사유를 셉니다. 응답에 못 싣는 만큼 지표에서라도 보여야 합니다.
    observe_validation_failure(request, [str(error.get("type", "invalid")) for error in errors])
    if request.url.path.startswith(receipt_router.prefix):
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    body = validation_error_body(errors)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST, content=body.model_dump(mode="json")
    )


async def handle_receipt_error(request: Request, exc: Exception) -> JSONResponse:
    """영수증 처리 실패를 계약된 500 본문으로 바꿉니다.

    성공 응답과 같은 구조를 유지하고 `error` 만 더합니다. 호출부가 두 모양을 나눠
    다루지 않아도 됩니다. 본문에 OCR 원문은 싣지 않고, 프론트가 보낸 receipt_id 를
    그대로 돌려주어 어느 영수증이 실패했는지 짝지을 수 있게 합니다.
    """
    error = cast(ReceiptError, exc)
    logger.error("receipt failed code=%s receipt_id=%s", error.code, error.receipt_id)
    body = ReceiptErrorResponse(
        receipt_id=error.receipt_id,
        error=ReceiptErrorDetail(code=error.code, message=error.user_message),
    )
    return JSONResponse(status_code=RECEIPT_FAILURE_STATUS, content=body.model_dump(mode="json"))


def create_app() -> FastAPI:
    """설정을 읽어 로깅을 켜고 도메인 라우터를 붙입니다.

    임포트 시점이 아니라 호출 시점에 설정을 읽습니다. 환경변수가 없는
    상태에서 모듈만 임포트해도 실패하지 않아야 테스트 수집이 됩니다.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="aiServer", version=CONTRACT_VERSION, lifespan=lifespan)
    add_request_logging(app)
    add_request_metrics(app)
    # 서비스가 세어 온 내부 카운터(삼킨 예외)를 지표로 내보냅니다 (DB 전환 M-07).
    monitor.watch_counters(recommend_service.counters)
    # 백엔드 주소가 있으면 실서빙, 없으면 목업입니다(`serving.build`).
    app.state.live_serving = serving.build(settings)
    monitor.watch_catalog(app.state.live_serving)
    app.add_exception_handler(ReceiptError, handle_receipt_error)
    app.add_exception_handler(RequestValidationError, handle_missing_field)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        """프로세스가 살아 있는지만 봅니다. 무거운 자원을 건드리지 않습니다."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> JSONResponse:
        """OCR 워커가 모델을 다 올린 뒤에만 200 입니다. 그 전에는 트래픽을 받지 않습니다."""
        if not receipt.is_ready():
            return JSONResponse(status_code=NOT_READY_STATUS, content={"status": "loading"})
        return JSONResponse(content={"status": "ok"})

    @app.get("/metrics", include_in_schema=False, dependencies=[Depends(verify_scraper_key)])
    def metrics() -> Response:
        """Prometheus 가 긁어 가는 자리. 요청 지표와 추천 엔진 지표가 함께 나갑니다."""
        body, content_type = render()
        return Response(content=body, media_type=content_type)

    internal_only = [Depends(verify_internal_api_key)]
    app.include_router(receipt_router, dependencies=internal_only)
    app.include_router(recommend_router, dependencies=internal_only)
    app.include_router(monitoring_router, dependencies=internal_only)
    # 페이지는 빈 껍데기라 열어 둡니다. 숫자는 위의 요약에서만 나오고 그쪽은 키가 필요합니다.
    app.include_router(monitoring_page_router)

    logger.info(
        "aiServer started (contract=%s · db=%s · recommend=%s)",
        CONTRACT_VERSION,
        settings.db_name,
        "live" if app.state.live_serving is not None else "mock",
    )
    return app


def _log_warmup_result(task: asyncio.Task[None]) -> None:
    """워커 로딩 실패를 삼키지 않습니다. 실패하면 ready 가 영영 200 이 되지 않습니다."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("ocr pool warmup failed: %s", error, exc_info=error)
