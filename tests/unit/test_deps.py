"""내부 호출자 인증. 키가 없을 때와 틀렸을 때가 모두 401 이어야 합니다."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from deps import INTERNAL_API_KEY_HEADER, verify_internal_api_key


def _probe_client() -> TestClient:
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(verify_internal_api_key)])
    def probe() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_missing_key_is_rejected() -> None:
    assert _probe_client().get("/probe").status_code == 401


def test_wrong_key_is_rejected() -> None:
    response = _probe_client().get("/probe", headers={INTERNAL_API_KEY_HEADER: "wrong"})
    assert response.status_code == 401


def test_correct_key_passes() -> None:
    response = _probe_client().get("/probe", headers={INTERNAL_API_KEY_HEADER: "test-internal-key"})
    assert response.status_code == 200
