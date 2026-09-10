"""스테이지가 주고받는 도메인 모델. HTTP 계약은 schema.py 에 있고 여기는 엔진 내부 자료형입니다.

A 트랙과 합치기 위한 배치입니다. 양쪽이 각자 만든 `schema.py` 는 HTTP 계약만 남기고,
Stage 1~3 사이를 오가는 모델은 이 파일에 모으기로 했습니다(A 트랙 공유 문서 2절).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, ConfigDict, field_validator

from features.recommend.schema import FLAVOR_AXES, FlavorVector


class RecipeCandidate(BaseModel):
    """Stage 1 이 넘기는 레시피 한 건. recipe_feature 행을 repository 가 이 형태로 바꿉니다.

    엔진은 이 모델만 보고 DB 행 형식을 모릅니다. 집합 연산이 잦아 재료 ID 는 frozenset 입니다.
    """

    model_config = ConfigDict(frozen=True)

    recipe_id: int
    title: str
    essential_ids: frozenset[int]
    all_ids: frozenset[int]
    flavor_vec: FlavorVector
    popularity_score: float | None = None
    quality_score: float | None = None
    cook_minutes: int | None = None
    cuisine: str | None = None
    product_ids: tuple[int, ...] = ()

    @field_validator("flavor_vec", mode="before")
    @classmethod
    def _take_leading_axes(cls, value: object) -> object:
        """A 트랙의 flavor_vec 은 6축입니다. 엔진은 앞 3축(매움, 짠맛, 단맛)만 씁니다.

        6축 전부를 쓸지는 W3 쌍대비교 학습 뒤에 정합니다(A 트랙 D-11). 그때까지는
        저장은 6축, 계산은 3축입니다.
        """
        if isinstance(value, list | tuple) and len(value) > len(FLAVOR_AXES):
            return tuple(value[: len(FLAVOR_AXES)])
        return value


@dataclass(frozen=True)
class UserHistory:
    """DB 에서 읽어 오는 사용자 이력. 요청 본문에는 없는 것들입니다."""

    behavior_taste_vec: FlavorVector | None = None
    events_count: int = 0
    allergy_ingredient_ids: frozenset[int] = frozenset()
    avoid_ingredient_ids: frozenset[int] = frozenset()
    # 최근 7일 노출과 최근 14일 조리. 기간은 repository 가 자르고 여기서는 집합만 봅니다.
    recent_recipe_ids: frozenset[int] = frozenset()
    cooked_recipe_ids: frozenset[int] = frozenset()
    # 보유 재료의 대체재. 2단계 폴백에서만 보유로 칩니다.
    substitute_ids: frozenset[int] = frozenset()
    # 요리군별 Beta(alpha, beta) 사후 분포. 탐색 슬롯의 Thompson Sampling 이 씁니다.
    cuisine_priors: Mapping[str, tuple[float, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class UserContext:
    """입력 어댑터를 거친 내부 표준 문맥. 요청과 이력을 합친 것이며 엔진은 이것만 받습니다."""

    user_id: int
    pantry_ids: frozenset[int]
    expiring_ids: frozenset[int]
    taste_vec: FlavorVector
    top_k: int
    household_size: int = 1
    preferred_cuisines: frozenset[str] = frozenset()
    max_cook_minutes: int | None = None
    history: UserHistory = field(default_factory=UserHistory)


@dataclass(frozen=True)
class CorpusStats:
    """feature_stats 에서 읽는 코퍼스 통계. 없으면 해당 블록은 측정 불가로 뺍니다."""

    flavor_mean: FlavorVector | None = None
    ingredient_idf: Mapping[int, float] = field(default_factory=dict)
    ingredient_names: Mapping[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RankConfig:
    """가중치와 감점 계수. 착수 값은 PM-ENG-RECO-B-001 3절이며 Bradley-Terry 학습 뒤 바뀝니다.

    조합의 해시가 서빙 로그의 config_fingerprint 로 남습니다. 값 하나만 달라도 지문이
    달라져 Track C 가 그때의 점수 계산을 되살릴 수 있습니다.
    """

    # 5블록 가중치
    w_match: float = 0.29
    w_expiring: float = 0.15
    w_taste: float = 0.31
    w_quality: float = 0.15
    w_ctx: float = 0.10
    quality_popularity_share: float = 0.6
    # 맛 블록 신뢰도. 사용자 취향이 코퍼스 평균에서 이 거리(온보딩 한 단계 = 0.25)만큼은
    # 떨어져 있어야 맛 유사도를 전폭 반영합니다. 그 안이면 거리에 비례해 0.5 쪽으로 눌러,
    # 전부 "보통" 을 고른 사용자의 잡음이 순위를 흔들지 않게 합니다.
    taste_min_norm: float = 0.25
    # 맛 사유 문구를 쓰려면 어느 축이든 평균과 이만큼(온보딩 반 단계)은 달라야 합니다.
    taste_reason_min_deviation: float = 0.125
    # 곱연산 감점
    penalty_recent: float = 0.7
    penalty_cooked: float = 0.5
    avoid_multiplier: float = 2.0
    avoid_cap: float = 0.8
    # Stage 1 후보군
    max_missing: int = 2
    max_missing_relaxed: int = 4
    min_candidates: int = 20
    candidate_limit: int = 500
    # Stage 3 재정렬
    mmr_lambda: float = 0.7
    # MMR 은 점수 상위 이만큼만 봅니다. 후보 500건 전부를 보면 재정렬이 지연시간의 70% 였고,
    # λ=0.7 에서 200위 밖 후보가 뽑히는 일은 없었습니다.
    mmr_pool_size: int = 200
    exploration_ratio: float = 0.2
    exploration_pool_size: int = 20
    # 탐색에 쓸 잔여 풀이 슬롯 수의 이 배수보다 작으면 슬롯을 줄입니다. 억지로 채우면
    # 후보 24건인 사용자에게 점수 0.19 짜리 잔여물이 3위에 섭니다.
    exploration_min_pool_ratio: int = 2
    # 맛 블록 점수가 이 아래면 미경험 맛 영역으로 보고 탐색 대상에 넣습니다.
    # 0.4 는 중심화 코사인 -0.2 에 해당합니다.
    novel_taste_max: float = 0.4
    # Thompson 슬롯의 노출 확률을 추정하는 몬테카를로 표본 수. 요리군 5개면 32 로도 ±0.09 안입니다.
    propensity_samples: int = 32
    # 피드백 루프
    ema_gamma: float = 0.2
    warm_event_count: int = 20

    def weights(self) -> dict[str, float]:
        return {
            "match": self.w_match,
            "expiring": self.w_expiring,
            "taste": self.w_taste,
            "quality": self.w_quality,
            "ctx": self.w_ctx,
        }

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.md5(payload, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class ScoredCandidate:
    """Stage 2 결과. 블록 점수를 남겨 두어 사유 문구와 서빙 로그가 다시 계산하지 않습니다."""

    candidate: RecipeCandidate
    missing_ids: tuple[int, ...]
    # None 은 측정 불가. 분자와 분모에서 함께 빠진 블록입니다.
    blocks: Mapping[str, float | None]
    base_score: float
    score: float


@dataclass(frozen=True)
class ServedItem:
    """Stage 3 결과. 최종 순위와 탐색 여부, IPS 보정용 노출 확률의 역수입니다."""

    scored: ScoredCandidate
    rank: int
    is_exploration: bool
    propensity: float
