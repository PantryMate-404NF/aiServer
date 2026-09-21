"""추천 도메인의 입출력 경계. 요청 파싱과 응답 직렬화만 담당합니다.

02 의 3.2 — **여기서는 데이터 가공을 하지 않습니다.**
스키마 검증(FastAPI 가 합니다) → 서비스 호출 → 상태코드 결정. 그게 전부입니다.

라우터 객체는 하나입니다. 경로 앞자리(`/v1`, `/v1/users`, `/health`)가 서로 달라
`prefix` 로 묶이지 않으므로 각 경로를 전부 씁니다. 노출되는 URL 은 이전과 같습니다.

⬜ 01 의 7.2 는 `/health/live`(프로세스)와 `/health/ready`(초기화 완료)로 나누라고
   합니다. 지금은 `/health` 하나입니다 — 나누면 계약이 바뀌므로 `schema.py` 와
   `docs/reco/design/05_API_SPEC.md` 를 함께 고쳐야 합니다 (미합의).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from features.recommend.engine import mock
from features.recommend.evaluation import monitor
from features.recommend.profile_store import load_presented_menu
from features.recommend.schema import (
    EventAck,
    EventBatchIn,
    HealthOut,
    IngredientSearchOut,
    OnboardingIn,
    OnboardingOut,
    PantryIn,
    PantryOut,
    PresentedItem,
    PresentedOut,
    RecipeSearchOut,
    RecommendationLogOut,
    RecommendRequest,
    RecommendResponse,
    TasteAxesOut,
    TasteAxisOut,
)
from features.recommend.serving import CatalogNotReadyError, LiveServing
from infra import db

logger = logging.getLogger(__name__)

router = APIRouter()


def _observe(record: Callable[[], None]) -> None:
    """관측을 남깁니다. **실패해도 응답은 그대로 나갑니다** — 지표가 틀리는 것과 추천이 안
    나가는 것은 무게가 다릅니다. 실패는 삼키지 않고 로그에 남깁니다.
    """
    try:
        record()
    except (ValueError, TypeError, KeyError, AttributeError):
        logger.exception("monitoring failed")


def _live(request: Request) -> LiveServing | None:
    """실서빙. 백엔드 주소가 설정돼 있을 때만 있습니다(`serving.build`). 없으면 목업이 답합니다.

    목업은 계약을 그대로 흉내 내는 개발용입니다. 대시보드와 프론트가 엔진을 기다리지 않게 합니다.
    """
    live = getattr(request.app.state, "live_serving", None)
    return live if isinstance(live, LiveServing) else None


def _read_log(request: Request, request_id: UUID) -> RecommendationLogOut | None:
    live = _live(request)
    return mock.read_log(request_id) if live is None else live.read_log(request_id)


@router.get("/health", response_model=HealthOut, tags=["health"])
def health() -> HealthOut:
    # `db.healthy()` 는 실패를 예외로 올리지 않고 False 를 돌려줍니다.
    return mock.health_payload(db_ok=db.healthy())


@router.post("/v1/recommend", response_model=RecommendResponse, tags=["recommend"])
def recommend(req: RecommendRequest, request: Request) -> RecommendResponse:
    live = _live(request)
    if live is None:
        response = mock.build_recommendation(req)
    else:
        try:
            response = live.recommend(req)
        except CatalogNotReadyError as error:
            # 목업으로 대신하지 않습니다. 목업의 레시피 번호는 백엔드 DB 에 없는 번호입니다.
            # 503 이면 백엔드가 자기 인기순으로 대신합니다.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    # 운영 호출은 추적을 빼고 받습니다(include_trace=false). 추적은 로그에 남아 있어 함께 넘깁니다.
    log = _read_log(request, response.request_id)
    _observe(lambda: monitor.observe_recommendation(req, response, log))
    return response


@router.get(
    "/v1/recommendations/{request_id}",
    response_model=RecommendationLogOut,
    tags=["recommend"],
)
def get_log(request_id: UUID, request: Request) -> RecommendationLogOut:
    # 404 판정은 여기서 합니다 — 서비스는 없으면 None 을 돌려줄 뿐입니다 (02 의 3.2).
    log = _read_log(request, request_id)
    if log is None:
        raise HTTPException(404, "request_id 를 찾을 수 없습니다")
    return log


@router.post("/v1/events", response_model=EventAck, tags=["events"])
def events(batch: EventBatchIn, request: Request) -> EventAck:
    """행동 로그 수집.

    `impression` 은 클라이언트가 보내지 않습니다 — `/v1/recommend` 가 서버측에서
    자동 기록합니다 (설계 3-2). 클라이언트에 맡기면 새로고침·세션 만료로 누락되고,
    랭킹 학습의 negative 샘플이 사라집니다.
    """
    # 받았다는 답은 계약의 검증 그대로입니다(목업과 실서빙이 같습니다). 실서빙은 거기에 더해
    # 조리 · 저장 · 클릭 · 별점을 그 사용자의 취향에 반영합니다.
    ack = mock.ack_events(batch)
    live = _live(request)
    if live is not None:
        live.record_events(batch, ack)

    def slot_of(request_id: object, recipe_id: int | None) -> str:
        log = _read_log(request, request_id) if isinstance(request_id, UUID) else None
        return monitor.slot_in_log(log, recipe_id)

    _observe(lambda: monitor.observe_events(batch, ack, slot_of))
    return ack


@router.get("/v1/ingredients/search", response_model=IngredientSearchOut, tags=["search"])
def search_ingredients(q: str, limit: int = 5) -> IngredientSearchOut:
    return mock.search_ingredients(q, limit)


@router.get("/v1/recipes/search", response_model=RecipeSearchOut, tags=["search"])
def search_recipes(
    q: str,
    limit: int = 20,
    user_id: int | None = None,
    max_missing: int | None = None,
) -> RecipeSearchOut:
    return mock.search_recipes(q, limit, user_id, max_missing)


@router.get("/v1/users/{user_id}/pantry", response_model=PantryOut, tags=["pantry"])
def get_pantry(user_id: int) -> PantryOut:
    return mock.read_pantry(user_id)


@router.put("/v1/users/{user_id}/pantry", response_model=PantryOut, tags=["pantry"])
def put_pantry(user_id: int, body: PantryIn) -> PantryOut:
    return mock.replace_pantry(user_id, body)


@router.get("/v1/onboarding/presented", response_model=PresentedOut, tags=["onboarding"])
def get_presented() -> PresentedOut:
    """온보딩에 보여줄 음식 목록. 화면이 하드코딩하지 않게 여기서 받아 갑니다."""
    version, rows = load_presented_menu()
    return PresentedOut(
        list_version=version,
        items=[PresentedItem(name=name, family=family) for name, family in rows],
    )


@router.get("/v1/onboarding/taste-axes", response_model=TasteAxesOut, tags=["onboarding"])
def get_taste_axes() -> TasteAxesOut:
    """맛 척도로 무엇을 보내야 하는지. 부르는 쪽이 베껴 두지 않게 여기서 받아 갑니다."""
    labels = {"spicy": "매움", "salty": "짠맛", "sweet": "단맛"}
    return TasteAxesOut(
        axes=[TasteAxisOut(key=key, label=label) for key, label in labels.items()],
    )


@router.post("/v1/onboarding/{user_id}", response_model=OnboardingOut, tags=["onboarding"])
def put_onboarding(user_id: int, body: OnboardingIn, request: Request) -> OnboardingOut:
    """온보딩 5문항 저장. 가입 직후 1회."""
    live = _live(request)
    saved = (
        mock.save_onboarding(user_id, body) if live is None else live.save_onboarding(user_id, body)
    )
    _observe(lambda: monitor.observe_onboarding(saved))
    return saved
