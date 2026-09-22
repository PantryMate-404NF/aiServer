"""`/metrics` 와 요청 지표, 관리자 페이지가 앱 경계에서 계약대로 도는지."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from features.recommend.evaluation import monitor
from utils.metrics import REGISTRY

KEY = "test-internal-key"
HEADERS = {"X-Internal-Api-Key": KEY}
RECOMMEND = {"user_id": 7, "pantry": [], "allergies": []}


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def client() -> TestClient:
    return TestClient(main.create_app())


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"X-Internal-Api-Key": "wrong"}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Authorization": KEY}, 401),
        (HEADERS, 200),
        ({"Authorization": f"Bearer {KEY}"}, 200),
    ],
)
def test_the_scrape_endpoint_takes_the_internal_key_in_either_header(
    headers: dict[str, str], status: int
) -> None:
    """Prometheus 는 어느 판에서나 Bearer 를 붙일 수 있습니다. 키는 하나입니다."""
    assert client().get("/metrics", headers=headers).status_code == status


def test_the_scrape_carries_both_request_and_engine_metrics() -> None:
    http = client()
    http.post("/v1/recommend", json=RECOMMEND, headers=HEADERS)

    response = http.get("/metrics", headers=HEADERS)

    assert response.headers["content-type"].startswith("text/plain")
    assert 'http_requests_total{method="POST",route="/v1/recommend",status="200"}' in response.text
    assert "reco_requests_total{" in response.text
    assert "reco_feature_items_total{" in response.text


def test_a_route_is_labelled_by_its_template_not_by_its_value() -> None:
    """`/v1/users/123/pantry` 를 그대로 라벨에 쓰면 사용자 수만큼 시계열이 생깁니다."""
    route = "/v1/users/{user_id}/pantry"
    before = value("http_requests_total", method="GET", route=route, status="200")

    client().get("/v1/users/123456/pantry", headers=HEADERS)

    assert value("http_requests_total", method="GET", route=route, status="200") - before == 1
    assert "123456" not in client().get("/metrics", headers=HEADERS).text


def test_a_path_that_matches_nothing_shares_one_label() -> None:
    before = value("http_requests_total", method="GET", route="unmatched", status="404")

    client().get("/wp-login.php")
    client().get("/.env")

    assert value("http_requests_total", method="GET", route="unmatched", status="404") - before == 2


def test_the_scraper_and_the_health_probes_do_not_count_as_traffic() -> None:
    http = client()
    http.get("/health/live")
    http.get("/metrics", headers=HEADERS)

    text = http.get("/metrics", headers=HEADERS).text

    assert 'route="/metrics"' not in text
    assert 'route="/health/live"' not in text


def test_a_rejected_request_is_counted_with_its_reason() -> None:
    before = value("http_validation_failures_total", route="/v1/recommend", code="missing")
    rejected = value("http_requests_total", method="POST", route="/v1/recommend", status="400")

    client().post("/v1/recommend", json={"user_id": 7}, headers=HEADERS)

    after = value("http_validation_failures_total", route="/v1/recommend", code="missing")
    assert after - before == 2
    now = value("http_requests_total", method="POST", route="/v1/recommend", status="400")
    assert now - rejected == 1


def test_the_receipt_route_counts_its_reason_even_though_its_body_is_empty() -> None:
    """응답에 사유를 싣지 않는 경로일수록 지표에서는 보여야 합니다."""
    route = "/v1/ocr/receipt"
    before = value("http_validation_failures_total", route=route, code="missing")

    response = client().post(route, headers=HEADERS, files={"file": ("r.jpg", b"x", "image/jpeg")})

    assert response.status_code == 400 and response.content == b""
    assert value("http_validation_failures_total", route=route, code="missing") - before == 1


def test_a_failing_observer_does_not_break_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """지표가 틀리는 것과 추천이 안 나가는 것은 무게가 다릅니다."""

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ValueError("label mismatch")

    monkeypatch.setattr(monitor, "observe_recommendation", broken)

    response = client().post("/v1/recommend", json=RECOMMEND, headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["items"]


def test_the_summary_needs_the_key_and_the_page_does_not() -> None:
    http = client()

    assert http.get("/v1/admin/monitoring/summary").status_code == 401
    page = http.get("/admin/monitoring")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    # 페이지는 빈 껍데기입니다. 키도 숫자도 들어 있지 않고 바깥 자원을 부르지 않습니다.
    assert KEY not in page.text
    assert "https://" not in page.text and "<script src" not in page.text


def test_the_summary_reflects_what_was_just_served() -> None:
    http = client()
    before = http.get("/v1/admin/monitoring/summary", headers=HEADERS).json()["kpis"]["requests"]

    http.post("/v1/recommend", json={**RECOMMEND, "allergies": ["아황산류"]}, headers=HEADERS)
    summary = http.get("/v1/admin/monitoring/summary", headers=HEADERS).json()

    assert summary["kpis"]["requests"] - before == 1
    assert summary["engine"] in ("mock", "mixed")
    assert summary["findings"][0]["title"] == "막지 못한 알레르기 라벨이 들어왔습니다"
    assert len(summary["features"]) == 17


def test_the_page_only_asks_for_numbers_the_summary_has() -> None:
    """페이지는 없는 키를 물어도 에러 없이 "–" 를 그립니다. 이름이 바뀌면 조용히 빈 카드입니다."""
    import re

    from features.recommend.evaluation import diagnosis

    page = client().get("/admin/monitoring").text
    asked = set(re.findall(r'\["([a-z0-9_]+)", "', page))

    assert len(asked) >= 15
    assert asked <= set(diagnosis.summarize({}).kpis)
