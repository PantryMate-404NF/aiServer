"""랭킹이 받는 입력. 요청·DB·코퍼스에서 온 값을 한 자리에 모읍니다.

`stage.Candidate` 는 ① Retrieval 이 SQL 에서 뽑아 온 것이라 recipe_id·부족 재료·
coverage 뿐입니다. 점수를 매기려면 그 레시피의 맛·인기·조리시간이 더 필요한데,
그것은 `recipe_feature` 의 다른 칸입니다. 그 부분만 `RecipeFeature` 로 받습니다.

사용자 쪽 값은 요청 본문에 없습니다. A 트랙의 서빙 경로는 `user_pantry_ids()` 와
`expand_user_allergens()` 가 SQL 안에서 유도하므로(01 1-7), 여기서도 요청이 아니라
repository 가 읽어 온 값을 받습니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from features.recommend.engine import taste
from features.recommend.engine.taste import FlavorVector


@dataclass(frozen=True)
class RecipeFeature:
    """`recipe_feature` 한 행 중 랭킹이 읽는 부분. repository 가 이 형태로 바꿉니다.

    엔진은 이 모델만 보고 DB 행 형식을 모릅니다. 집합 연산이 잦아 재료 ID 는 frozenset 입니다.
    데이터가 아직 없는 칸(`cuisine`, `dish_type`, `season_score`, `difficulty`)은 None 이며,
    그 피처는 계산에서 빠집니다. 데이터가 오면 코드를 고치지 않아도 켜집니다.
    """

    recipe_id: int
    title: str = ""
    essential_ids: frozenset[int] = frozenset()
    all_ids: frozenset[int] = frozenset()
    flavor_vec: FlavorVector = (None,) * taste.AXIS_COUNT
    popularity_score: float | None = None
    quality_score: float | None = None
    cook_minutes: int | None = None
    cuisine: str | None = None
    dish_type: str | None = None
    season_score: float | None = None
    difficulty: float | None = None
    product_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class UserHistory:
    """DB 에서 읽어 오는 사용자 이력. 요청 본문에는 없는 것들입니다."""

    behavior_taste_vec: FlavorVector | None = None
    events_count: int = 0
    #: 선호·기피 재료 (`user_ingredient_pref`).
    liked_ingredient_ids: frozenset[int] = frozenset()
    avoid_ingredient_ids: frozenset[int] = frozenset()
    #: 최근 7일 노출과 최근 14일 조리. 기간은 repository 가 자르고 여기서는 집합만 봅니다.
    recent_recipe_ids: frozenset[int] = frozenset()
    cooked_recipe_ids: frozenset[int] = frozenset()
    #: 최근 조리한 레시피의 재료 집합. f_cooccur 가 이것과의 유사도를 봅니다.
    cooked_ingredient_sets: tuple[frozenset[int], ...] = ()
    #: 클러스터별 노출·반응 관측. 탐색 슬롯의 Thompson 이 씁니다 (`user_cluster_stat`).
    cluster_seen: Mapping[int, int] = field(default_factory=dict)
    cluster_hits: Mapping[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CorpusStats:
    """`feature_stats` 에서 읽는 코퍼스 통계. 없으면 그 피처는 측정 불가입니다."""

    flavor_mean: FlavorVector | None = None
    ingredient_idf: Mapping[int, float] = field(default_factory=dict)
    ingredient_names: Mapping[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UserContext:
    """랭킹이 보는 사용자. 요청과 이력을 합친 것이며 엔진은 이것만 받습니다."""

    user_id: int
    pantry_ids: frozenset[int] = frozenset()
    expiring_ids: frozenset[int] = frozenset()
    taste_vec: FlavorVector = (None,) * taste.AXIS_COUNT
    max_cook_minutes: int | None = None
    preferred_cuisines: frozenset[str] = frozenset()
    preferred_dish_types: frozenset[str] = frozenset()
    skill_level: float | None = None
    history: UserHistory = field(default_factory=UserHistory)


def build_context(
    *,
    user_id: int,
    pantry_ids: Sequence[int] = (),
    expiring_ids: Sequence[int] = (),
    onboarding_taste: Sequence[float | None] | None = None,
    history: UserHistory | None = None,
    warm_event_count: int = 20,
    max_cook_minutes: int | None = None,
    preferred_cuisines: Sequence[str] = (),
    preferred_dish_types: Sequence[str] = (),
    skill_level: float | None = None,
) -> UserContext:
    """온보딩 취향과 행동 취향을 섞어 랭킹이 쓸 문맥을 만듭니다.

    온보딩은 지금 앞 3축만 옵니다. `as_vector` 가 뒤 3축을 None 으로 채우므로 그 축은
    맛 계산에서 빠지고, 나중에 6축이 오면 그대로 쓰입니다.
    """
    past = history or UserHistory()
    onboarding = taste.as_vector(onboarding_taste)
    effective = taste.effective_taste(
        onboarding, past.behavior_taste_vec, past.events_count, warm_event_count
    )
    return UserContext(
        user_id=user_id,
        pantry_ids=frozenset(pantry_ids),
        expiring_ids=frozenset(expiring_ids),
        taste_vec=effective,
        max_cook_minutes=max_cook_minutes,
        preferred_cuisines=frozenset(preferred_cuisines),
        preferred_dish_types=frozenset(preferred_dish_types),
        skill_level=skill_level,
        history=past,
    )
