"""애플리케이션 진입점. uv run uvicorn main:create_app --factory 로 실행합니다."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from config import get_settings
from features.receipt.router import router as receipt_router
from features.recommend.router import router as recommend_router
from utils.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """설정을 읽어 로깅을 켜고 도메인 라우터를 붙입니다.

    임포트 시점이 아니라 호출 시점에 설정을 읽습니다. 환경변수가 없는
    상태에서 모듈만 임포트해도 실패하지 않아야 테스트 수집이 됩니다.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="aiServer")
    app.include_router(receipt_router)
    app.include_router(recommend_router)

    logger.info("aiServer started (db=%s)", settings.db_name)
    return app
