"""추천 도메인이 경계 밖으로 내보내는 데이터 구조.

두 종류를 담습니다.

  1. **HTTP 계약** — 대시보드와 Reco API 사이의 유일한 접점입니다.
     경로 8개 · 오퍼레이션 9개입니다 (pantry 가 GET·PUT).
     09-03 에 온보딩이 늘었고 AI 파트가 3명이 됐지만 남은 기간이 3주라
     표면은 그대로 둡니다.
  2. **JSONB 칸의 속 형식** — 표가 있다고 양식이 정해진 것은 아닙니다.
     `jsonb` 칸은 무엇이든 받으므로, 넣는 사람과 읽는 사람이 다른 모양을 써도
     DB 는 아무 말도 하지 않습니다. 실제로 09-02 에 A 와 C 가
     `normalization_queue.suggested` 를 배열과 객체로 각각 적어 놓은 것이
     발견됐습니다. 그대로 짰으면 시더가 넣은 것을 화면이 못 읽었을 것이고,
     **에러가 아니라 빈 화면**으로 나타났을 것입니다.

     **두 사람 이상이 주고받는 칸만** 담습니다. 혼자 쓰는 자유 칸
     (`batch_run.params` · `event_log.context` · `recipe.raw_json`)은 묶지
     않습니다 — 그런 것까지 계약으로 만들면 형식을 다듬을 때마다 계약을
     고쳐야 합니다.

스테이지끼리 주고받는 모델은 `stage.py`, 열거형과 상수는 `enums.py` 입니다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from features.recommend.enums import CONTRACT_VERSION, EventType
from features.recommend.stage import RankedItem, StageTrace


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─────────────────────────────────────────────────────────────────
# POST /v1/recommend
# ─────────────────────────────────────────────────────────────────
class RecommendRequest(_Base):
    user_id: int
    #: 주의: 소급 불가 — impression 은 서버가 이 요청에서 자동 기록하므로(3-2),
    #:    여기 없으면 impression 전량(이벤트의 95%)에 세션이 비게 된다.
    #: 주의: 패턴을 입력에서 검증한다. 없으면 잘못된 값이 그대로 통과해
    #:    응답 조립 중 로그 계약에서 터진다 — 422 여야 할 것이 500 이 된다
    #:    (09-03 발견·수정). c- 실사용자 · g- 게스트 · d- 개발·디버거·시딩.
    session_id: str | None = Field(
        default=None,
        pattern=r"^[cgd]-",
        description="클라이언트 발급 (c-{user}-{uuid12}). 30분 무활동 시 갱신",
    )
    top_k: int = Field(default=20, ge=1, le=100)
    max_missing: int = Field(default=2, ge=0, le=10)
    max_minutes: int | None = None

    #: 디버거에서 모델을 골라 비교할 때. None 이면 현재 서빙 모델.
    model_version: str | None = None
    #: 디버거 전용 — 가중치를 즉석에서 바꿔 ablation(R9) 을 돌린다.
    weight_override: dict[str, float] | None = None
    #: 🔑 Team-Draft Interleaving — 이 모델과 결과를 섞어 한 목록으로 낸다 (설계 5-7-2).
    #:    유저 100명에서 A/B 테스트는 검정력이 없다. 같은 유저가 두 랭커를 동시에
    #:    평가하므로 유저 간 분산이 사라지고 필요 표본이 1~2 자릿수 줄어든다.
    interleave_with: str | None = Field(
        default=None, description="비교 대상 model_version. 지정 시 items[].team 이 채워진다"
    )

    include_trace: bool = Field(
        default=True, description="False 면 응답에서 trace 를 빼고 DB 에는 그대로 남긴다"
    )
    context: dict[str, str | int | None] = Field(
        default_factory=dict, description="{hour, weekday, device, source_screen}"
    )


class RecommendResponse(_Base):
    contract_version: str = CONTRACT_VERSION
    request_id: UUID = Field(description="event 기록 시 이 값을 함께 보낸다")
    user_id: int
    model_version: str
    #: 🔑 이 응답을 만든 실효 가중치. `weight_override` 가 반영된 값이다.
    #:    없으면 로그만 보고 점수를 재현할 수 없다 (features 만으로는 부족).
    weights: dict[str, float] = Field(default_factory=dict)
    items: list[RankedItem]
    trace: StageTrace | None = None
    served_at: datetime


# ─────────────────────────────────────────────────────────────────
# POST /v1/events
# ─────────────────────────────────────────────────────────────────
class EventIn(_Base):
    """impression 은 서버가 자동 기록한다 (설계 3-2). 클라이언트는 보내지 않는다."""

    user_id: int
    event_type: EventType
    recipe_id: int | None = None
    value: float | None = Field(default=None, description="rating 점수 · dwell time(sec)")
    request_id: UUID | None = Field(
        default=None, description="🔴 없으면 학습 라벨과 추천 로그를 이을 수 없다"
    )
    position: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description=(
            "🔴 없으면 position bias 보정이 영구 불가. **1-base** — final_rank 와 동일 기준"
        ),
    )
    #: 주의: 소급 불가. 세션 = 한 번의 앉은 자리. 시퀀스 모델이 학습하는 단위다.
    #:    지금 안 남기면 나중에 SASRec/BERT4Rec 을 시도할 데이터가 영원히 없다.
    session_id: str | None = Field(
        default=None, pattern=r"^[cgd]-", description="클라이언트가 발급. 30분 무활동 시 갱신"
    )
    context: dict[str, str | int | None] = Field(default_factory=dict)


class EventBatchIn(_Base):
    events: list[EventIn] = Field(min_length=1, max_length=200)


class EventAck(_Base):
    #: 주의: 계약 검사를 통과해 받아들인 개수다 — 보낸 건수 − rejected.
    #:    저장에 성공한 개수가 아니다. DB 가 흡수한 중복도 여기 반영되지 않는다.
    accepted: int = Field(description="보낸 건수 − rejected. 저장 성공 개수가 아니다")
    rejected: int = 0
    errors: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# 냉장고 · 재료
# ─────────────────────────────────────────────────────────────────
class PantryItemIn(_Base):
    ingredient_id: int
    quantity: float | None = None
    unit: str | None = None
    #: 주의: 구매일. 소비기한 추정의 기준점이다 (09-02 신설).
    #: 사용자가 이것만 넣으면 서버가 재료별 소비기한을 더해 `expires_at` 을 만든다.
    #: 앱 등록일과 다르다 — 마트에서 사고 사흘 뒤에 넣으면 사흘을 공짜로 벌어준다.
    purchased_at: date | None = Field(default=None, description="구매일. 소비기한 추정의 기준점")
    #: 소비기한(use-by). 유통기한(sell-by)이 아니다.
    #: 사용자가 직접 넣으면 그것이 이긴다 — 추정보다 우선한다.
    expires_at: date | None = Field(
        default=None, description="소비기한. 안 주면 purchased_at + 재료별 일수로 추정"
    )


# ─────────────────────────────────────────────────────────────────
# 온보딩 — 09-02 신설. 이 계약이 없어서 가중치 0.27 을 저장할 곳이 없었다.
# ─────────────────────────────────────────────────────────────────
class OnboardingIn(_Base):
    """온보딩 5문항 응답 (S0 ② 확정 문항).

    🔴 **선택한 레시피와 척도 원본을 그대로 받는다.** `taste_vec` 은 이것들의
       평균이라 결과만 저장하면 **다시 계산할 수 없다** — 시드가 바뀌면
       `flavor_vec` 이 바뀌고 `taste_vec` 도 따라 바뀌어야 하는데, 원본이 없으면
       유저를 다시 모아야 한다 (실제로 09-02 에 시드 2건을 고쳤다).
       저장 위치는 `user_vector.onboarding_picks` · `onboarding_scales`.
    """

    #: 제시 20개 중 고른 것의 인덱스 (seeds/onboarding_recipes.yaml 의 presented 순서).
    #: 확정 문항은 3개지만 개수는 서버가 강제하지 않는다 — 프론트가 정한다.
    picks: list[int] = Field(min_length=1, max_length=20)
    #: 맛 척도 3축 [매움, 짠맛, 단맛] 각 0~4. 순서가 계약이다.
    scales: list[int] = Field(min_length=3, max_length=3)
    #: 알러지 — 그룹명과 재료 ID 를 둘 다 받는다 (안전 관련이라 이중화).
    #: 주의: 서버는 이것을 `severity='allergy'` 로 저장한다. DB 기본값 'avoid' 에
    #:    맡기면 그룹 확산이 조용히 꺼져 본인이 적은 재료만 막힌다.
    allergy_groups: list[str] = Field(default_factory=list)
    allergy_ingredient_ids: list[int] = Field(default_factory=list)
    #: 기피 재료 (알러지가 아님). `user_ingredient_pref` 에 score=-0.8 로 저장.
    avoid_ingredient_ids: list[int] = Field(default_factory=list, max_length=3)
    #: 가구원 수. 선택 항목이라 없을 수 있다.
    household_size: int | None = Field(default=None, ge=1, le=10)

    @field_validator("scales")
    @classmethod
    def _scale_range(cls, v: list[int]) -> list[int]:
        if any(not 0 <= x <= 4 for x in v):
            raise ValueError("척도는 0~4 다")
        return v


class OnboardingOut(_Base):
    """저장 결과. 프론트는 완료 여부만 알면 된다."""

    user_id: int
    #: 산출된 맛 취향 6축. 확인용으로만 돌려준다.
    taste_vec: list[float] = Field(min_length=6, max_length=6)
    #: 알러지로 차단될 재료 수 (그룹 전개 후). 사용자에게 보여주면 신뢰가 는다.
    n_blocked_ingredients: int


class PantryRemoval(_Base):
    """사라진 재료 하나와 그 사유 (S0 ⑤ · 2026-09-02).

    🔴 **UI 방식을 서버가 알 필요 없다.** 버튼 두 개로 받든 저장 후 모달로 받든
    클라이언트 사정이고, 서버는 사유만 받는다. 그래야 UI 를 바꿔도 API 가 안 바뀐다.
    """

    ingredient_id: int
    #: consumed 다 씀 · discarded 상해서 버림 · unknown 물었는데 건너뜀
    #: 아예 안 물었으면 이 항목을 보내지 않는다 (DB 에서 NULL 로 남는다).
    reason: Literal["consumed", "discarded", "unknown"]


class PantryIn(_Base):
    items: list[PantryItemIn]
    #: 주의: 소급 불가 (07 E-3 ③). 이번 교체로 사라진 재료의 사유.
    #:    PUT 이 전체 교체라 클라이언트가 diff 를 계산해 실어 보낸다.
    #:    "다 씀"과 "버림"은 부호가 반대인 신호라 합치면 영원히 못 나눈다 —
    #:    소비기한 낭비율·shelf_life 검증·소진 시퀀스가 전부 여기 달려 있다.
    removed: list[PantryRemoval] = Field(default_factory=list)


class PantryItemOut(PantryItemIn):
    name: str
    days_left: int | None = None
    is_staple: bool = False


class PantryOut(_Base):
    user_id: int
    items: list[PantryItemOut]
    staple_count: int = Field(description="자동 포함된 staple 수. 유저가 등록한 것이 아님 (결정 2)")


# ─────────────────────────────────────────────────────────────────
# GET /v1/recipes/search — 자연어 레시피 검색
#
# 임베딩이 아니면 불가능한 유일한 기능이다 (설계 6-4-3).
# "매콤한 국물요리" 같은 텍스트 쿼리를 받는다.
# ─────────────────────────────────────────────────────────────────
class RecipeSearchIn(_Base):
    q: str = Field(min_length=1, max_length=100, description="자연어 쿼리")
    limit: int = Field(default=20, ge=1, le=100)
    user_id: int | None = Field(default=None, description="주면 냉장고 재료 정보를 함께 반환한다")
    max_missing: int | None = Field(default=None, description="주면 만들 수 있는 것만 필터링한다")


class RecipeHit(_Base):
    recipe_id: int
    title: str
    score: float = Field(description="쿼리와의 코사인 유사도")
    cuisine: str | None = None
    cook_minutes: int | None = None
    #: user_id 를 준 경우에만 채워진다
    missing_count: int | None = None
    missing_names: list[str] = Field(default_factory=list)


class RecipeSearchOut(_Base):
    query: str
    hits: list[RecipeHit]
    model_version: str = Field(description="임베딩 모델 버전")
    latency_ms: int
    degraded: bool = Field(
        default=False, description="임베딩 인덱스 미구축 등으로 제목 검색으로 폴백했다"
    )


class IngredientHit(_Base):
    ingredient_id: int
    name: str
    score: float
    method: str = Field(description="exact | alias | jamo_trgm")
    category: str | None = None


class IngredientSearchOut(_Base):
    query: str
    hits: list[IngredientHit]
    #: 못 찾았을 때 유저에게 보여줄 안내 (설계 4-9). 조용히 무시하지 않는다.
    not_found_message: str | None = None


# ─────────────────────────────────────────────────────────────────
# GET /v1/recommendations/{request_id} — 로그 탐색기
# ─────────────────────────────────────────────────────────────────
class RecommendationLogOut(_Base):
    """`recommendation_log` 1행. **DDL 과 필드가 일치해야 한다** (v2.9 동기화).

    v2.8 DDL 개정으로 컬럼 7개가 늘었는데 이 계약이 따라오지 않아
    **DB 에 칸은 있는데 채울 계약이 없는** 상태였다. 여기서 맞춘다.
    """

    request_id: UUID
    user_id: int
    #: 주의: 세션 식별자. 소급 불가 — 시퀀스 모델(SASRec 등)의 전제다 (설계 3-2).
    #:    규약: c-{user_id}-{uuid4hex12} 클라이언트 · g-{user_id}-{YYYYMMDDHHMI} 서버 갭
    #: 주의: 접두어가 트래픽 종류를 가른다 — DDL 의 CHECK 와 같아야 한다.
    #:   c- 실사용자 · g- 게스트 · d- 개발·디버거·시딩
    #: d- 를 빼먹었더니 디버거가 규약대로 부를 때 500 이 났다 (09-03 수정).
    session_id: str | None = Field(default=None, pattern=r"^[cgd]-")
    model_version: str
    mlflow_run_id: str | None = None

    # ── 점수 재현 3종 (설계 5-2-2-1) ──────────────────────────────
    #: 기준 가중치를 되찾는 열쇠. `scoring_config` 레지스트리를 가리킨다.
    config_hash: str | None = None
    #: 주의: 웜 전환 계수 α = min(1, n_events/n_warm). 실효 w 는 유저·요청마다 다르다.
    warm_alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    #: 주의: 어느 코퍼스 평균 μ 로 f_taste 를 계산했나 (`feature_stats`).
    stats_version: int | None = None

    pantry_snapshot: list[int]
    #: 주의: 소급 불가. `pantry_snapshot` 은 id 만 담아 f_expiring 원값을 검증할 수 없다.
    #:    [{ingredient_id, quantity, unit, expires_at, expires_at_source}]
    pantry_detail: list[dict[str, Any]] | None = None
    allergy_snapshot: list[int] = Field(default_factory=list)

    #: 주의: 소급 불가. Interleaving 승패 귀속 — 'A' 가 어느 모델이었나.
    #:    [{team, model_version, mlflow_run_id}]. 단일 정책이면 None.
    policies: list[dict[str, Any]] | None = None

    stage_trace: StageTrace
    served: list[int]
    total_latency_ms: int
    created_at: datetime

    #: 주의: `weights` 를 뺐다 (v2.9). v1.9 는 "실효 가중치를 저장해야 재현된다"고 했으나,
    #:    5-2-2-1 이 재분배를 나눗셈으로 바꾸면서 `config_hash` + `warm_alpha` +
    #:    `features` 의 None 패턴으로 유도된다. 저장할 이유가 사라졌다.


# ─────────────────────────────────────────────────────────────────
# 에러 — 추천은 절대 실패하지 않는다 (설계 5-6)
# ─────────────────────────────────────────────────────────────────
class ErrorOut(_Base):
    """4xx 전용. 추천 경로의 5xx 는 폴백으로 대체되어 발생하지 않아야 한다.

    최악의 경우에도 인기순 Top-N 을 돌려주고 `trace.totals.degraded=True` 로 표시한다.
    빈 목록은 유저에게 장애로 보이고 디버깅 정보도 남지 않는다.
    """

    error: str
    detail: str | None = None
    request_id: UUID | None = None


class HealthOut(_Base):
    status: str = "ok"
    contract_version: str = CONTRACT_VERSION
    model_version: str | None = None
    db: bool = True
    redis: bool = True


# ─────────────────────────────────────────────────────────────────
# normalization_queue.suggested — A 시더가 쓰고 C 화면이 읽는다 (결정 D-2)
# ─────────────────────────────────────────────────────────────────
class QueueCandidate(_Base):
    """검수 화면에 버튼으로 놓일 후보 하나."""

    #: 주의: 반드시 사전에 있는 표제어. 없는 이름을 만들지 않는다 —
    #:    검수자가 누르면 그대로 사전에 들어가기 때문이다.
    name: str = Field(min_length=1)
    #: 0~1. 화면이 정렬·표시에만 쓴다. 자동 확정하지 않는다 —
    #: 실측에서 임계 0.6 재현율이 0% 였다(정탐 0.143 vs 오탐 0.118).
    score: float = Field(ge=0.0, le=1.0)
    method: Literal["exact", "alias", "rule", "jamo_trgm"]


class QueueSuggestion(_Base):
    """`normalization_queue.suggested` 의 전체 모양.

    배열이 아니라 **객체**인 이유는 나중에 칸을 늘릴 수 있어야 해서다.
    `[["매실청", 0.62, "trgm"]]` 은 위치로 의미를 기억해야 하고
    `blocked_by` 를 넣으려면 전부 고쳐야 한다.
    """

    candidates: list[QueueCandidate] = Field(default_factory=list, max_length=10)
    #: 구조적으로 막힌 이유. 없으면 None.
    blocked_by: Literal["hyponym", "sibling", "unrelated"] | None = None


# ─────────────────────────────────────────────────────────────────
# recommendation_log.pantry_detail — B 가 쓰고 C 가 읽는다
# ─────────────────────────────────────────────────────────────────
class PantrySnapshotItem(_Base):
    """요청 시점 냉장고 한 칸.

    🔴 `pantry_snapshot` 배열은 재료 id 만 담아 `f_expiring` 원값을 검증할 수 없다.
       그래서 상세를 따로 남긴다 — 소비기한이 유저 입력인지 추정인지까지.
    """

    ingredient_id: int
    quantity: float | None = None
    unit: str | None = None
    expires_at: str | None = None  # ISO date
    expires_at_source: Literal["user", "estimated", "unknown"] = "estimated"


# ─────────────────────────────────────────────────────────────────
# recommendation_log.policies — 두 정책을 섞어 낼 때만 채운다
# ─────────────────────────────────────────────────────────────────
class PolicyArm(_Base):
    """섞어 낸 정책 한 쪽.

    🔴 `recipe_ids` 가 없으면 **어느 쪽이 이겼는지 셀 수 없다.**
       모델 이름만으로는 승패가 안 나온다 — 클릭된 것이 어느 팀 것이었는지가 필요하다.
    """

    team: Literal["A", "B"]
    model_version: str
    mlflow_run_id: str | None = None
    recipe_ids: list[int] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# data_quality_snapshot.match_method_dist — A 와 C 가 나눠 쓴다 (결정 D-1)
# ─────────────────────────────────────────────────────────────────
class QualityExtra(_Base):
    """품질 기록에 곁들이는 값들.

    🔴 표본 수를 반드시 남긴다. 없으면 배치 개선 없이도 추이선이 오르내려
       "좋아졌다" 로 잘못 읽힌다.
    """

    #: 몇 건을 보고 잰 값인가
    sample_n: int = Field(ge=0, alias="_sample_n")
    #: 파일에서 쟀나 DB 에서 쟀나 — 정의가 달라 섞으면 시계열이 꺾인다
    source: Literal["file", "db"] = Field(alias="_source")
    #: 로그 쓰기 실패 계측. 프로세스 메모리라 여기 찍어야 시계열이 남는다
    log_counters: dict[str, int] = Field(default_factory=dict, alias="_log_counters")

    model_config = ConfigDict(extra="allow", populate_by_name=True)
