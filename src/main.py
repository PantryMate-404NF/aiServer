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
from deps import verify_internal_api_key
from features import receipt
from features.receipt.router import router as receipt_router
from features.receipt.schema import ReceiptErrorDetail, ReceiptErrorResponse
from features.recommend.enums import CONTRACT_VERSION
from features.recommend.router import router as recommend_router
from infra import gemini
from utils.errors import ReceiptError
from utils.logging import add_request_logging, configure_logging

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
    try:
        yield
    finally:
        warmup.cancel()
        receipt.shutdown_pool()


async def handle_missing_field(request: Request, exc: Exception) -> Response:
    """필드가 빠진 요청은 400 입니다. FastAPI 기본값인 422 는 계약에 없습니다.

    본문을 비웁니다. 이 경계를 넘는 요청은 백엔드가 보낸 것이라 사용자에게 보일 문구가
    없고, 어느 필드가 빠졌는지는 로그에만 남깁니다.
    """
    logger.warning("request is missing required fields: %s", exc)
    return Response(status_code=status.HTTP_400_BAD_REQUEST)


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

    internal_only = [Depends(verify_internal_api_key)]
    app.include_router(receipt_router, dependencies=internal_only)
    app.include_router(recommend_router, dependencies=internal_only)

    logger.info("aiServer started (contract=%s · db=%s)", CONTRACT_VERSION, settings.db_name)
    return app


def _log_warmup_result(task: asyncio.Task[None]) -> None:
    """워커 로딩 실패를 삼키지 않습니다. 실패하면 ready 가 영영 200 이 되지 않습니다."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("ocr pool warmup failed: %s", error, exc_info=error)
