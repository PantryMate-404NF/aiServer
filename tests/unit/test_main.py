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


#: 추천 요청의 필수 둘. "없음" 은 빈 배열로 명시합니다 (2026-09-21 백엔드 합의).
BUNDLED: dict[str, object] = {"pantry": [], "allergies": []}


def _recommend_client() -> TestClient:
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER

    header = {INTERNAL_API_KEY_HEADER: get_settings().internal_api_key}
    return TestClient(main.create_app(), headers=header)


def test_an_unknown_field_is_named_in_the_400_body() -> None:
    """추천 계약은 모르는 필드 하나로 요청 전체를 거부합니다. 백엔드가 그 필드를 알아야 합니다."""
    response = _recommend_client().post("/v1/recommend", json={**BUNDLED, "user_id": 7, "topk": 20})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_failed"
    assert {"location": "body", "field": "topk", "code": "extra_forbidden"}.items() <= (
        body["fields"][0].items()
    )


def test_a_missing_field_is_named_in_the_400_body() -> None:
    response = _recommend_client().post("/v1/recommend", json={**BUNDLED, "top_k": 20})

    assert response.status_code == 400
    named = {(item["field"], item["code"]) for item in response.json()["fields"]}
    assert ("user_id", "missing") in named


def test_the_400_body_does_not_echo_what_was_sent() -> None:
    """보낸 값을 돌려주면 요청 본문이 응답과 호출 쪽 로그로 복사됩니다."""
    sent_value = "do-not-echo-8f3a2b1c"
    body = {**BUNDLED, "user_id": 7, "memo": sent_value}
    response = _recommend_client().post("/v1/recommend", json=body)

    assert response.status_code == 400
    assert sent_value not in response.text
    assert set(response.json()["fields"][0]) == {"location", "field", "code", "message"}


def test_a_nested_field_is_addressed_by_its_path() -> None:
    event = {"user_id": 7, "event_type": "cook", "recipe_id": 1, "occurred_at": "2026-09-21T19:32"}
    response = _recommend_client().post("/v1/events", json={"events": [event]})

    assert response.status_code == 400
    assert response.json()["fields"][0]["field"] == "events.0.occurred_at"


@pytest.mark.parametrize("missing", ["allergies", "pantry"])
def test_recommend_without_the_bundled_fields_is_400_not_a_silent_200(missing: str) -> None:
    """기본값을 두면 백엔드의 버그로 필드가 빠져도 200 이 나가고 알레르기 재료가 섞입니다."""
    body = {key: value for key, value in {**BUNDLED, "user_id": 7}.items() if key != missing}

    response = _recommend_client().post("/v1/recommend", json=body)

    assert response.status_code == 400
    assert (missing, "missing") in {(f["field"], f["code"]) for f in response.json()["fields"]}


def test_no_allergy_and_an_empty_fridge_are_said_with_empty_arrays() -> None:
    response = _recommend_client().post("/v1/recommend", json={**BUNDLED, "user_id": 7})

    assert response.status_code == 200
    assert response.json()["items"]


def test_the_fridge_carries_no_quantity() -> None:
    """백엔드는 수량을 관리하지 않습니다(MVP 범위 밖). 받지 않는 칸은 계약에 두지 않습니다."""
    pantry = [{"ingredient_id": 37, "quantity": 300, "unit": "g"}]

    response = _recommend_client().post(
        "/v1/recommend", json={"user_id": 7, "pantry": pantry, "allergies": []}
    )

    assert response.status_code == 400
    named = {(f["field"], f["code"]) for f in response.json()["fields"]}
    assert named == {("pantry.0.quantity", "extra_forbidden"), ("pantry.0.unit", "extra_forbidden")}


def test_what_arrived_is_visible_in_the_trace_and_the_log() -> None:
    """목업은 거르지 못하지만 무엇을 받았는지는 남깁니다. 모르는 라벨도 버리지 않습니다."""
    client = _recommend_client()
    body = {
        "user_id": 7,
        "pantry": [
            {"ingredient_id": 37, "purchased_at": "2026-09-18", "expires_at": "2026-09-23"},
            {"ingredient_id": 5, "purchased_at": "2026-09-20"},
        ],
        "allergies": ["우유", "아황산류"],
    }

    served = client.post("/v1/recommend", json=body).json()

    received = served["trace"]["stages"][0]["params"]
    assert received["pantry_received"] == 2
    assert received["allergy_labels"] == "우유, 아황산류"
    assert received["allergy_labels_unknown"] == "아황산류"
    log = client.get(f"/v1/recommendations/{served['request_id']}").json()
    assert log["pantry_snapshot"] == [37, 5]
    assert [row["expires_at_source"] for row in log["pantry_detail"]] == ["user", "unknown"]
