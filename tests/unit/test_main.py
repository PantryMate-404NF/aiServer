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


def _recommend_client() -> TestClient:
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER

    header = {INTERNAL_API_KEY_HEADER: get_settings().internal_api_key}
    return TestClient(main.create_app(), headers=header)


def test_an_unknown_field_is_named_in_the_400_body() -> None:
    """추천 계약은 모르는 필드 하나로 요청 전체를 거부합니다. 백엔드가 그 필드를 알아야 합니다."""
    response = _recommend_client().post("/v1/recommend", json={"user_id": 7, "topk": 20})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_failed"
    assert {"location": "body", "field": "topk", "code": "extra_forbidden"}.items() <= (
        body["fields"][0].items()
    )


def test_a_missing_field_is_named_in_the_400_body() -> None:
    response = _recommend_client().post("/v1/recommend", json={"top_k": 20})

    assert response.status_code == 400
    named = {(item["field"], item["code"]) for item in response.json()["fields"]}
    assert ("user_id", "missing") in named


def test_the_400_body_does_not_echo_what_was_sent() -> None:
    """보낸 값을 돌려주면 요청 본문이 응답과 호출 쪽 로그로 복사됩니다."""
    sent_value = "do-not-echo-8f3a2b1c"
    response = _recommend_client().post("/v1/recommend", json={"user_id": 7, "memo": sent_value})

    assert response.status_code == 400
    assert sent_value not in response.text
    assert set(response.json()["fields"][0]) == {"location", "field", "code", "message"}


def test_a_nested_field_is_addressed_by_its_path() -> None:
    event = {"user_id": 7, "event_type": "cook", "recipe_id": 1, "occurred_at": "2026-09-21T19:32"}
    response = _recommend_client().post("/v1/events", json={"events": [event]})

    assert response.status_code == 400
    assert response.json()["fields"][0]["field"] == "events.0.occurred_at"
