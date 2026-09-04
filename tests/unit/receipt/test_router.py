"""API 경계. 계약된 상태 코드와 본문 모양만 봅니다. 서비스는 가짜로 바꿉니다."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import config
import main
from features.receipt import router as receipt_router
from features.receipt.schema import ReceiptItem, ReceiptResponse
from utils.errors import LlmUnavailableError, OcrEmptyError

RECEIPT_ID = "01K4A7Q3ZV8XG2M5W9R1DTF6HJ"
PATH = "/v1/ocr/receipt"
HEADERS = {"X-Internal-Api-Key": "test-internal-key"}
FILE = {"file": ("receipt.jpg", b"binary", "image/jpeg")}

SUCCESS = ReceiptResponse(
    receipt_id=RECEIPT_ID,
    purchased_at=date(2026, 1, 30),
    items=[ReceiptItem(name="깐마늘"), ReceiptItem(name="야채듬뿍사각어묵")],
)


def _client(monkeypatch: pytest.MonkeyPatch, outcome: object) -> TestClient:
    async def _parse(receipt_id: str, data: bytes) -> ReceiptResponse:
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ReceiptResponse)
        return outcome

    monkeypatch.setattr(receipt_router.service, "parse_receipt_image", _parse)
    return TestClient(main.create_app(), raise_server_exceptions=False)


def test_success_body_matches_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SUCCESS)

    response = client.post(PATH, headers=HEADERS, data={"receipt_id": RECEIPT_ID}, files=FILE)

    assert response.status_code == 200
    assert response.json() == {
        "receipt_id": RECEIPT_ID,
        "purchased_at": "2026-01-30",
        "items": [
            {"name": "깐마늘", "ingredient_id": None},
            {"name": "야채듬뿍사각어묵", "ingredient_id": None},
        ],
    }


def test_missing_internal_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SUCCESS)

    response = client.post(PATH, data={"receipt_id": RECEIPT_ID}, files=FILE)

    assert response.status_code == 401


def test_missing_field_is_400_not_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """422 는 계약에 없습니다. 백엔드가 400 만 보고 자기 버그를 압니다."""
    client = _client(monkeypatch, SUCCESS)

    response = client.post(PATH, headers=HEADERS, files=FILE)

    assert response.status_code == 400
    assert response.content == b""


def test_ocr_failure_returns_the_receipt_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """프론트가 어느 영수증이 실패했는지 짝지을 수 있어야 합니다."""
    client = _client(monkeypatch, OcrEmptyError(RECEIPT_ID))

    response = client.post(PATH, headers=HEADERS, data={"receipt_id": RECEIPT_ID}, files=FILE)

    assert response.status_code == 500
    body = response.json()
    assert body["receipt_id"] == RECEIPT_ID
    assert body["error"]["code"] == "OCR_EMPTY"


def test_llm_failure_uses_its_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, LlmUnavailableError(RECEIPT_ID))

    response = client.post(PATH, headers=HEADERS, data={"receipt_id": RECEIPT_ID}, files=FILE)

    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"


def test_failure_body_has_the_same_shape_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """호출부가 성공과 실패에서 다른 모양을 다루지 않아도 되게 합니다."""
    client = _client(monkeypatch, OcrEmptyError(RECEIPT_ID))

    failure = client.post(PATH, headers=HEADERS, data={"receipt_id": RECEIPT_ID}, files=FILE).json()

    assert failure["purchased_at"] is None
    assert failure["items"] == []
    assert set(failure) == {"receipt_id", "purchased_at", "items", "error"}


def test_success_body_has_no_error_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """성공에는 error 가 없습니다. 있으면 호출부가 null 검사를 하나 더 하게 됩니다."""
    client = _client(monkeypatch, SUCCESS)

    body = client.post(PATH, headers=HEADERS, data={"receipt_id": RECEIPT_ID}, files=FILE).json()

    assert "error" not in body


def test_oversized_upload_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """앞단이 거르기로 되어 있지만 계약을 어긴 요청이 와도 서버가 버텨야 합니다."""
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")
    config.get_settings.cache_clear()
    client = _client(monkeypatch, SUCCESS)

    response = client.post(
        PATH,
        headers=HEADERS,
        data={"receipt_id": RECEIPT_ID},
        files={"file": ("receipt.jpg", b"x" * 100, "image/jpeg")},
    )

    assert response.status_code == 400


def test_absurdly_long_receipt_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """그대로 로그와 응답에 실리는 값이라 길이를 제한합니다."""
    client = _client(monkeypatch, SUCCESS)

    response = client.post(PATH, headers=HEADERS, data={"receipt_id": "0" * 500}, files=FILE)

    assert response.status_code == 400
