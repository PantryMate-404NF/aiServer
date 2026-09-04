"""진입점이 설정을 읽고 로깅을 켠 뒤 앱을 만드는지, 실패 응답이 계약대로인지."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

import main
from utils.errors import OcrEmptyError
from utils.logging import REQUEST_ID_HEADER

RECEIPT_ID = "01K4A7Q3ZV8XG2M5W9R1DTF6HJ"


def test_create_app_configures_logging_and_logs_startup(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """configure_logging 은 basicConfig(force=True) 로 caplog 핸들러를 지우므로 막습니다."""
    called: list[str] = []
    monkeypatch.setattr(main, "configure_logging", called.append)

    with caplog.at_level(logging.INFO):
        app = main.create_app()

    assert called == ["DEBUG"]
    assert app.title == "aiServer"
    assert "aiServer started" in caplog.text
    assert "aiserver_test" in caplog.text


def test_health_live_needs_no_internal_key() -> None:
    """기동 확인은 인증 앞단에 둡니다. 키 배포 전에도 살아 있는지 봐야 합니다."""
    response = TestClient(main.create_app()).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_receipt_error_becomes_contracted_500() -> None:
    app = main.create_app()

    @app.get("/boom")
    def boom() -> None:
        raise OcrEmptyError(RECEIPT_ID)

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["receipt_id"] == RECEIPT_ID
    assert body["error"]["code"] == "OCR_EMPTY"
    assert body["error"]["message"]
    assert "meta" not in body


def test_request_id_is_echoed_back() -> None:
    response = TestClient(main.create_app()).get(
        "/health/live", headers={REQUEST_ID_HEADER: "trace-1"}
    )

    assert response.headers[REQUEST_ID_HEADER] == "trace-1"
