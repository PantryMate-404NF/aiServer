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

from uuid import UUID

from fastapi import APIRouter, HTTPException

from features.recommend.engine import mock
from features.recommend.schema import (
    EventAck,
    EventBatchIn,
    HealthOut,
    IngredientSearchOut,
    OnboardingIn,
    OnboardingOut,
    PantryIn,
    PantryOut,
    RecipeSearchOut,
    RecommendationLogOut,
    RecommendRequest,
    RecommendResponse,
)
from infra import db

router = APIRouter()


@router.get("/health", response_model=HealthOut, tags=["health"])
def health() -> HealthOut:
    # `db.healthy()` 는 실패를 예외로 올리지 않고 False 를 돌려줍니다.
    return mock.health_payload(db_ok=db.healthy())


@router.post("/v1/recommend", response_model=RecommendResponse, tags=["recommend"])
def recommend(req: RecommendRequest) -> RecommendResponse:
    return mock.build_recommendation(req)


@router.get(
    "/v1/recommendations/{request_id}",
    response_model=RecommendationLogOut,
    tags=["recommend"],
)
def get_log(request_id: UUID) -> RecommendationLogOut:
    # 404 판정은 여기서 합니다 — 서비스는 없으면 None 을 돌려줄 뿐입니다 (02 의 3.2).
    log = mock.read_log(request_id)
    if log is None:
        raise HTTPException(404, "request_id 를 찾을 수 없습니다")
    return log


@router.post("/v1/events", response_model=EventAck, tags=["events"])
def events(batch: EventBatchIn) -> EventAck:
    """행동 로그 수집.

    `impression` 은 클라이언트가 보내지 않습니다 — `/v1/recommend` 가 서버측에서
    자동 기록합니다 (설계 3-2). 클라이언트에 맡기면 새로고침·세션 만료로 누락되고,
    랭킹 학습의 negative 샘플이 사라집니다.
    """
    return mock.ack_events(batch)


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


@router.post("/v1/onboarding/{user_id}", response_model=OnboardingOut, tags=["onboarding"])
def put_onboarding(user_id: int, body: OnboardingIn) -> OnboardingOut:
    """온보딩 5문항 저장. 가입 직후 1회."""
    return mock.save_onboarding(user_id, body)
