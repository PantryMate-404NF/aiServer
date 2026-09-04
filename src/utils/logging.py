"""로깅 설정과 요청 로깅. 애플리케이션 시작 시 한 번만 호출합니다."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
REQUEST_ID_HEADER = "X-Request-Id"
MISSING_REQUEST_ID = "-"

# 요청마다 값이 다르므로 인자로 넘기지 않고 문맥에 싣습니다. 인자로 넘기면 모든 단계
# 함수 시그니처에 로깅용 매개변수가 붙습니다.
request_id: ContextVar[str] = ContextVar("request_id", default=MISSING_REQUEST_ID)


class _RequestIdFilter(logging.Filter):
    """모든 로그 줄에 요청 추적 아이디를 채워 넣습니다. 없으면 하이픈입니다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    """루트 로거를 설정합니다. 모듈에서는 getLogger(__name__) 만 씁니다."""
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    for handler in logging.getLogger().handlers:
        handler.addFilter(_RequestIdFilter())


def add_request_logging(app: FastAPI) -> None:
    """요청마다 경로·상태·소요 시간을 남기고 X-Request-Id 를 응답에 되돌려 보냅니다.

    본문은 남기지 않습니다. 업로드 이미지와 OCR 원문에는 개인정보가 섞입니다.
    """

    @app.middleware("http")
    async def _log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, MISSING_REQUEST_ID)
        token = request_id.set(incoming)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "%s %s %d %.0fms",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
            response.headers[REQUEST_ID_HEADER] = incoming
            return response
        finally:
            # 접근 로그를 찍은 뒤에 되돌립니다. 먼저 되돌리면 그 줄만 아이디를 잃습니다.
            request_id.reset(token)
