"""실서빙의 흐름 (`serving.py`) — 동기화 · 요청 → 문맥 · 추천 · 온보딩 · 이벤트 · 라우터."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from features.recommend import service, serving
from features.recommend.backend_client import BackendClient
from features.recommend.enums import EventType
from features.recommend.policy import POLICY_ID, RankingPolicy
from features.recommend.profile_store import (
    PRESENTED_PATH,
    JsonProfileStore,
    load_presented_flavors,
)
from features.recommend.schema import (
    EventAck,
    EventBatchIn,
    EventIn,
    OnboardingIn,
    RecommendPantryItem,
    RecommendRequest,
)
from utils.errors import ExternalServiceError

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
HEADERS = {"X-Internal-Api-Key": "test-internal-key"}
NAMES = {1: "두부", 2: "새우", 3: "간장", 4: "소금", 5: "양파", 6: "우유", 7: "돼지고기", 8: "감자"}
INGREDIENTS = [
    {"ingredient_id": key, "name": name, "default_shelf_life_days": 5}
    for key, name in NAMES.items()
]


def recipes(count: int = 80) -> list[dict[str, object]]:
    """두부가 필수인 레시피 `count` 건. 셋에 하나는 새우가 들어갑니다.

    제목에 조리법을 적지 않습니다. 적으면 같은 요리의 판본으로 묶여(`engine/dish.py`) 목록이
    요리 가짓수만큼으로 줄어듭니다 — 그 동작은 `test_dish.py` 가 봅니다.
    """
    return [
        {
            "recipe_id": 1000 + i,
            "title": f"{'새우 ' if i % 3 == 0 else ''}메뉴 {i}",
            "cuisine_type": "KOREAN",
            "cooking_time": 20,
            "difficulty": "EASY",
            "ingredients": [
                {"ingredient_id": 1, "is_main": True},
                {"ingredient_id": 2 if i % 3 == 0 else 5, "is_main": False},
            ],
            "popularity": {"scrap_count": i},
        }
        for i in range(count)
    ]


class FakeBackend:
    """백엔드 대역. `down` 을 켜면 500 으로 답합니다."""

    def __init__(self) -> None:
        self.recipes = recipes()
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(500)
        if request.url.path.endswith("ingredients"):
            return httpx.Response(200, json={"items": INGREDIENTS})
        return httpx.Response(200, json={"items": self.recipes, "next_cursor": None})


def live(
    backend: Callable[[httpx.Request], httpx.Response],
    tmp_path: Path,
    sink: serving.RecommendationSink | None = None,
) -> serving.LiveServing:
    policy = RankingPolicy()
    client = BackendClient(
        "http://backend.test",
        "k",
        ingredients_path="/internal/ai/ingredients",
        recipes_path="/internal/ai/recipes",
        timeout_sec=5,
        page_size=1000,
        transport=httpx.MockTransport(backend),
    )
    personas = service.PersonaService(
        store=JsonProfileStore(tmp_path),
        presented=load_presented_flavors(PRESENTED_PATH),
        policy=policy,
    )
    return serving.LiveServing(
        client,
        personas,
        policy,
        sync_interval_sec=3600,
        retry_interval_sec=1,
        clock=lambda: NOW,
        sink=sink,
    )


def request(**overrides: object) -> RecommendRequest:
    fields: dict[str, object] = {
        "user_id": 7,
        "top_k": 10,
        "pantry": [{"ingredient_id": 1}],
        "allergies": [],
    }
    fields.update(overrides)
    return RecommendRequest(**fields)


def test_nothing_is_recommended_before_the_first_sync(tmp_path: Path) -> None:
    """목업으로 대신하지 않습니다. 목업의 레시피 번호는 백엔드 DB 에 없는 번호입니다."""
    engine = live(FakeBackend(), tmp_path)

    assert engine.state().ready is False
    with pytest.raises(serving.CatalogNotReadyError):
        engine.recommend(request())


def test_the_served_recipes_are_the_backends_and_the_engine_is_the_real_one(tmp_path: Path) -> None:
    engine = live(FakeBackend(), tmp_path)
    engine.sync_once()

    response = engine.recommend(request())

    assert response.model_version == POLICY_ID
    assert len(response.items) == 10
    assert all(1000 <= item.recipe_id < 1080 for item in response.items)
    assert response.trace is not None
    assert [stage.name.value for stage in response.trace.stages] == [
        "retrieval",
        "ranking",
        "rerank",
    ]
    assert response.trace.stages[-1].params["serving_mode"] == "real"
    assert str(response.trace.stages[-1].params["feature_version"]).startswith("backend-")


def test_the_allergies_in_the_request_cut_the_list(tmp_path: Path) -> None:
    engine = live(FakeBackend(), tmp_path)
    engine.sync_once()

    response = engine.recommend(request(allergies=["새우", "아황산류"], top_k=20))

    served = [item.recipe_id for item in response.items]
    assert served and all((recipe_id - 1000) % 3 != 0 for recipe_id in served)
    assert response.trace is not None
    received = response.trace.stages[0].params
    assert received["allergy_labels_unknown"] == "아황산류"
    assert received["allergy_blocked_ingredients"] == 1
    assert response.trace.stages[0].filters["allergy_cut"] > 0
    log = engine.read_log(response.request_id)
    assert log is not None and log.allergy_snapshot == [2] and log.pantry_snapshot == [1]


def test_the_requests_max_missing_is_where_the_search_starts(tmp_path: Path) -> None:
    """계약의 `max_missing` 입니다. 읽지 않으면 "지금 만들 수 있는 것만" 이 조용히 무시됩니다."""
    backend = FakeBackend()
    lacking = [
        {**recipe, "recipe_id": 5000 + i, "title": f"모자란 메뉴 {i}"}
        for i, recipe in enumerate(recipes())
    ]
    for recipe in lacking:
        recipe["ingredients"] = [
            {"ingredient_id": 1, "is_main": True},
            {"ingredient_id": 7, "is_main": True},
        ]
        recipe["popularity"] = {"scrap_count": 10_000}
    backend.recipes = [*backend.recipes, *lacking]
    engine = live(backend, tmp_path)
    engine.sync_once()

    strict = engine.recommend(request(max_missing=0))
    loose = engine.recommend(request())

    assert strict.trace is not None and loose.trace is not None
    assert strict.trace.stages[0].params["max_missing"] == 0
    assert all(item.missing_count == 0 for item in strict.items)
    assert loose.trace.stages[0].params["max_missing"] == 2
    assert any(item.missing_count == 1 for item in loose.items)


def test_production_calls_get_no_trace_but_the_log_keeps_it(tmp_path: Path) -> None:
    engine = live(FakeBackend(), tmp_path)
    engine.sync_once()

    response = engine.recommend(request(include_trace=False))

    assert response.trace is None
    log = engine.read_log(response.request_id)
    assert log is not None and len(log.stage_trace.stages) == 3
    assert log.served == [item.recipe_id for item in response.items]


def test_a_failed_sync_keeps_yesterdays_catalog(tmp_path: Path) -> None:
    """반쯤 읽은 사전으로 서빙하는 것보다 어제의 사전으로 서빙하는 편이 낫습니다."""
    backend = FakeBackend()
    engine = live(backend, tmp_path)
    version = engine.sync_once().version
    backend.down = True

    with pytest.raises(ExternalServiceError):
        engine.sync_once()

    assert engine.state().version == version
    assert len(engine.recommend(request()).items) == 10


def test_the_sync_loop_survives_a_failure_it_did_not_expect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """스레드가 죽으면 사전이 다시는 갱신되지 않는데 에러가 없습니다. 받아서 남기고 다시 합니다."""
    engine = live(FakeBackend(), tmp_path)
    before = service.counters().get("catalog_sync_failed", 0)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("예상하지 못한 것")

    monkeypatch.setattr(serving.catalog, "build_catalog", broken)
    assert engine._attempt() == 1
    assert engine.state().last_error == "RuntimeError"
    assert service.counters()["catalog_sync_failed"] - before == 1

    monkeypatch.undo()
    assert engine._attempt() == 3600
    assert engine.state().ready and engine.state().last_error is None


def test_an_empty_answer_from_the_backend_does_not_replace_the_catalog(tmp_path: Path) -> None:
    backend = FakeBackend()
    engine = live(backend, tmp_path)
    engine.sync_once()
    backend.recipes = []

    with pytest.raises(serving.CatalogNotReadyError):
        engine.sync_once()

    assert engine.state().recipes == 80


def test_an_ingredient_the_catalog_does_not_know_is_counted(tmp_path: Path) -> None:
    engine = live(FakeBackend(), tmp_path)
    engine.sync_once()
    before = service.counters().get("pantry_ingredient_unknown", 0)

    engine.recommend(request(pantry=[{"ingredient_id": 1}, {"ingredient_id": 999_999}]))

    assert service.counters()["pantry_ingredient_unknown"] - before == 1


def test_the_log_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serving, "LOG_CAPACITY", 3)
    engine = live(FakeBackend(), tmp_path)
    engine.sync_once()

    ids = [engine.recommend(request()).request_id for _ in range(5)]

    assert [engine.read_log(i) is not None for i in ids] == [False, False, True, True, True]
    assert engine.read_log(uuid4()) is None


def test_every_recommendation_is_also_written_to_a_file(tmp_path: Path) -> None:
    """메모리의 로그는 재배포 때 사라집니다. 노출 기록은 나중에 복원할 수 없습니다."""
    folder = tmp_path / "logs"
    engine = live(FakeBackend(), tmp_path, serving.RecommendationSink(folder))
    engine.sync_once()

    first = engine.recommend(request(include_trace=False))
    engine.recommend(request())

    written = (folder / "recommendations-20260922.jsonl").read_text(encoding="utf-8")
    records = [json.loads(line) for line in written.splitlines()]
    assert len(records) == 2
    assert records[0]["log"]["request_id"] == str(first.request_id)
    assert [item["recipe_id"] for item in records[0]["items"]] == records[0]["log"]["served"]
    assert len(records[0]["log"]["stage_trace"]["stages"]) == 3
    assert all(item["propensity"] is not None for item in records[0]["items"])


def test_a_log_file_that_cannot_be_written_does_not_stop_the_recommendation(
    tmp_path: Path,
) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("폴더 자리에 파일이 있습니다", encoding="utf-8")
    engine = live(FakeBackend(), tmp_path, serving.RecommendationSink(blocked))
    engine.sync_once()
    before = service.counters().get("reco_log_write_failed", 0)

    assert len(engine.recommend(request()).items) == 10

    assert service.counters()["reco_log_write_failed"] - before == 1


def test_a_taste_store_that_cannot_be_written_is_counted(tmp_path: Path) -> None:
    """지속 볼륨이 없거나 권한이 없을 때입니다. 500 으로 나가되 관리자 페이지가 원인을 봅니다."""
    blocked = tmp_path / "blocked"
    blocked.write_text("폴더 자리에 파일이 있습니다", encoding="utf-8")
    engine = live(FakeBackend(), blocked)
    before = service.counters().get("persona_store_error", 0)

    with pytest.raises(OSError):
        engine.save_onboarding(7, OnboardingIn(picks=["불고기", "계란말이"]))

    assert service.counters()["persona_store_error"] - before == 1


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"ingredient_id": 1, "expires_at": "2026-09-24"}, [1]),
        ({"ingredient_id": 1, "expires_at": "2026-09-22"}, [1]),
        ({"ingredient_id": 1, "expires_at": "2026-09-30"}, []),
        ({"ingredient_id": 1, "expires_at": "2026-09-20"}, []),
        ({"ingredient_id": 1, "purchased_at": "2026-09-19"}, [1]),
        ({"ingredient_id": 1, "purchased_at": "2026-09-22"}, []),
        ({"ingredient_id": 9, "purchased_at": "2026-09-19"}, []),
        ({"ingredient_id": 1}, []),
        ({"ingredient_id": 1, "purchased_at": "2026-09-01", "expires_at": "2026-09-23"}, [1]),
    ],
)
def test_expiring_means_within_three_days_by_date_or_by_shelf_life(
    item: dict[str, object], expected: list[int]
) -> None:
    """직접 받은 날짜가 우선이고, 없으면 구매일 + 재료별 일수입니다. 둘 다 없으면 모릅니다."""
    pantry = [RecommendPantryItem(**item)]

    assert serving.expiring_ingredients(pantry, {1: 5}, date(2026, 9, 22)) == expected


def test_onboarding_is_stored_and_shapes_the_next_recommendation(tmp_path: Path) -> None:
    engine = live(FakeBackend(), tmp_path)
    engine.sync_once()
    cold = engine.recommend(request(user_id=42))

    saved = engine.save_onboarding(
        42,
        OnboardingIn(
            picks=["불고기", "계란말이"],
            preferred_cuisines=["ETC"],
            allergy_groups=["새우", "아황산류"],
        ),
    )
    warm = engine.recommend(request(user_id=42))

    assert saved.preferred_cuisines == ["asian_other"]
    assert saved.unmapped_allergens == ["아황산류"]
    assert saved.n_blocked_ingredients == 1
    assert JsonProfileStore(tmp_path).load(42) is not None
    assert cold.trace is not None and warm.trace is not None
    assert (
        cold.trace.stages[-1].params["persona_source"]
        != (warm.trace.stages[-1].params["persona_source"])
    )


def test_events_reach_the_taste_profile(tmp_path: Path) -> None:
    engine = live(FakeBackend(), tmp_path)
    engine.sync_once()
    served = engine.recommend(request(user_id=43))
    batch = EventBatchIn(
        events=[
            EventIn(
                user_id=43,
                event_type=EventType.COOK,
                recipe_id=served.items[0].recipe_id,
                request_id=served.request_id,
                position=1,
            )
        ]
    )

    ack = engine.record_events(batch, EventAck(accepted=1, rejected=0, errors=[]))

    assert ack.accepted == 1
    profile = JsonProfileStore(tmp_path).load(43)
    assert profile is not None and len(profile.events) == 1


def test_without_a_backend_address_the_mock_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BACKEND_BASE_URL", raising=False)

    app = main.create_app()
    body = {"user_id": 7, "pantry": [], "allergies": []}
    response = TestClient(app).post("/v1/recommend", json=body, headers=HEADERS)

    assert app.state.live_serving is None
    assert response.json()["model_version"].startswith("mock")
    monkeypatch.setattr("infra.db.healthy", lambda: True)
    health = TestClient(app).get("/health", headers=HEADERS).json()
    assert health["model_version"].startswith("mock")


def test_with_a_backend_address_the_route_is_503_until_the_catalog_arrives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """503 이면 백엔드가 자기 인기순으로 대신합니다. 200 에 가짜 번호를 싣는 것보다 낫습니다."""
    import config

    monkeypatch.setenv("BACKEND_BASE_URL", "http://backend.test")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    app = main.create_app()
    http = TestClient(app)
    body = {"user_id": 7, "top_k": 5, "pantry": [{"ingredient_id": 1}], "allergies": ["새우"]}

    assert isinstance(app.state.live_serving, serving.LiveServing)
    assert http.post("/v1/recommend", json=body, headers=HEADERS).status_code == 503

    app.state.live_serving = live(FakeBackend(), tmp_path)
    app.state.live_serving.sync_once()
    served = http.post("/v1/recommend", json=body, headers=HEADERS)

    assert served.status_code == 200
    assert served.json()["model_version"] == POLICY_ID
    # `/health` 도 같은 이름을 답합니다. 목업의 이름이면 배포 설정이 빠진 것으로 읽힙니다.
    monkeypatch.setattr("infra.db.healthy", lambda: True)
    assert http.get("/health", headers=HEADERS).json()["model_version"] == POLICY_ID
    request_id = served.json()["request_id"]
    assert http.get(f"/v1/recommendations/{request_id}", headers=HEADERS).status_code == 200
