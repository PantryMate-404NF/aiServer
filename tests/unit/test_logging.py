"""로깅 설정."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

import main
from utils import logging as utils_logging
from utils.logging import configure_logging


def test_configure_logging_sets_root_level() -> None:
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
    configure_logging("INFO")
    assert logging.getLogger().level == logging.INFO


def test_request_id_is_available_to_every_log_line() -> None:
    """미들웨어가 문맥에 심어야 파이프라인 안쪽 로그도 같은 아이디로 묶입니다."""
    app = main.create_app()

    @app.get("/probe-request-id")
    def probe() -> dict[str, str]:
        return {"seen": utils_logging.request_id.get()}

    response = TestClient(app).get("/probe-request-id", headers={"X-Request-Id": "trace-9"})

    assert response.json() == {"seen": "trace-9"}


def test_request_id_defaults_when_the_header_is_absent() -> None:
    app = main.create_app()

    @app.get("/probe-default-id")
    def probe() -> dict[str, str]:
        return {"seen": utils_logging.request_id.get()}

    assert TestClient(app).get("/probe-default-id").json() == {"seen": "-"}


def test_log_format_carries_the_request_id() -> None:
    """포맷에서 빠지면 문맥에 심어 봐야 로그에 남지 않습니다."""
    assert "%(request_id)s" in utils_logging.LOG_FORMAT
