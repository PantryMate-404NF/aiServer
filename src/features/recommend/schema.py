"""추천 도메인의 HTTP 계약. 스테이지 사이의 모델은 stage.py 에 있습니다."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 맛 벡터의 축 순서 (매움, 짠맛, 단맛). A 트랙 설계 결정 D-11 의 온보딩 척도 순서이며
# recipe_feature.flavor_vec 6축(매움·짠맛·단맛·신맛·감칠맛·기름짐)의 앞 3축과 같습니다.
# 요청의 선호도, 레시피 flavor_vec, user_vector 테이블이 전부 이 순서입니다.
FLAVOR_AXES = ("spicy", "salty", "sweet")
TASTE_LEVEL_MAX = 4
FlavorVector = tuple[float, float, float]
EventType = Literal["click", "cook", "dismiss"]


class TastePreference(BaseModel):
    """온보딩에서 받은 3축 맛 선호도. 0~4 단계입니다."""

    model_config = ConfigDict(extra="ignore")

    spicy_level: int = Field(default=2, ge=0, le=TASTE_LEVEL_MAX)
    sweet_level: int = Field(default=2, ge=0, le=TASTE_LEVEL_MAX)
    salty_level: int = Field(default=2, ge=0, le=TASTE_LEVEL_MAX)

    def as_vector(self) -> FlavorVector:
        """레시피 flavor_vec 과 같은 0~1 척도로 바꿉니다."""
        return (
            self.spicy_level / TASTE_LEVEL_MAX,
            self.salty_level / TASTE_LEVEL_MAX,
            self.sweet_level / TASTE_LEVEL_MAX,
        )


class RecommendRequest(BaseModel):
    """백엔드가 보내는 추천 요청. 규약 밖 필드는 무시하고 비정형 값은 보정합니다.

    백엔드 DTO 가 바뀌어도 랭킹 코어가 깨지지 않게 여기서 흡수합니다. 보정 규칙을
    하나 더하면 그 규칙이 깨질 때 실패하는 테스트를 하나 남깁니다.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: int
    pantry_ingredient_ids: list[int]
    expiring_ingredient_ids: list[int] = Field(
        default_factory=list, description="D-3 소비기한 임박 식재료 ID"
    )
    taste_preference: TastePreference = Field(default_factory=TastePreference)
    household_size: int = Field(default=1, ge=1)
    preferred_cuisines: list[str] = Field(default_factory=list)
    max_cook_minutes: int | None = Field(default=None, ge=1)
    allergy_group_codes: list[str] = Field(
        default_factory=list, description="식약처 19종 표준 알레르기 코드"
    )
    top_k: int = Field(default=20, ge=1, le=50)

    @field_validator("household_size", mode="before")
    @classmethod
    def _parse_household_size(cls, value: object) -> int:
        """'4인 가구' 같은 문자열에서 숫자를 뽑습니다. 못 뽑으면 1인입니다."""
        if isinstance(value, int | float):
            return max(1, int(value))
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            return max(1, int(match.group())) if match else 1
        return 1

    @field_validator("preferred_cuisines", mode="before")
    @classmethod
    def _parse_cuisines(cls, value: object) -> list[str]:
        """리스트가 아니라 '한식, 양식' 같은 문자열로 와도 받습니다."""
        if isinstance(value, str):
            parts = (part.strip() for part in re.split(r"[,/ ]+", value))
            return [part for part in parts if part]
        if isinstance(value, list | tuple | set):
            names = (str(item).strip() for item in value if item is not None)
            return [name for name in names if name]
        return []


class RecommendedItem(BaseModel):
    """추천 목록의 한 줄."""

    rank: int = Field(ge=1, description="서빙 순서. match_score 순이 아니며 재정렬하지 않습니다")
    recipe_id: int
    recipe_title: str
    match_score: float = Field(ge=0.0, le=1.0)
    cook_minutes: int | None = None
    missing_ingredient_ids: list[int] = Field(default_factory=list)
    missing_count: int = Field(ge=0)
    reason: str
    matched_product_ids: list[int] = Field(default_factory=list)
    is_exploration: bool = False


class RecommendMeta(BaseModel):
    """폴백 적용 여부와 지연시간. Track C 가 서빙 로그와 짝지을 때 씁니다."""

    degraded: bool
    fallback_stage: str
    candidate_count: int
    latency_ms: int
    config_fingerprint: str


class RecommendResponse(BaseModel):
    """성공 응답. 폴백이 걸려도 200 이며 meta.degraded 로만 알립니다.

    recommendations 의 순서가 곧 서빙 순서입니다. MMR 다양성과 탐색 슬롯의 무작위 배치가
    만든 순서라, 호출자가 match_score 로 다시 정렬하면 그 설계가 무효가 됩니다.
    """

    request_id: UUID
    recommendations: list[RecommendedItem]
    meta: RecommendMeta


class FeedbackEventRequest(BaseModel):
    """추천 결과에 대한 사용자 행동 1건."""

    model_config = ConfigDict(extra="ignore")

    user_id: int
    request_id: UUID
    recipe_id: int
    position: int = Field(ge=1, le=50, description="노출 순위. Position Bias 보정용")
    event_type: EventType
    timestamp: datetime


class FeedbackEventResponse(BaseModel):
    success: bool
    updated_taste_vector: list[float]
