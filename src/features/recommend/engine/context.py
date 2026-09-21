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
from typing import Any

from features.recommend.engine import taste
from features.recommend.engine.persona import Persona
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import normalize_cuisine


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
    #: `recipe_feature.cuisine_family` (`enums.CuisineFamily`). 세분 축이 아니라 거친 축입니다 -
    #: 사용자가 고르는 것도 거친 축이라, 세분 코드를 넣으면 한 건도 안 맞습니다.
    cuisine: str | None = None
    dish_type: str | None = None
    season_score: float | None = None
    difficulty: float | None = None
    product_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class UserHistory:
    """DB 에서 읽어 오는 사용자 이력. 요청 본문에는 없는 것들입니다.

    맛 취향은 여기 없습니다. `engine/persona.py` 가 저장소의 원본에서 만들어 `UserContext.persona`
    로 들어옵니다.
    """

    #: 선호·기피 재료 (`user_ingredient_pref`).
    liked_ingredient_ids: frozenset[int] = frozenset()
    avoid_ingredient_ids: frozenset[int] = frozenset()
    #: 최근 7일 노출과 최근 14일 조리. 기간은 repository 가 자르고 여기서는 집합만 봅니다.
    recent_recipe_ids: frozenset[int] = frozenset()
    cooked_recipe_ids: frozenset[int] = frozenset()
    #: 최근 조리한 레시피의 재료 집합. f_cooccur 가 이것과의 유사도를 봅니다.
    cooked_ingredient_sets: tuple[frozenset[int], ...] = ()
    #: 위 집합과 같은 순서의 레시피 제목. 사유 문구("지난번 만드신 X 와 비슷해요")가 씁니다.
    #: 없으면 그 사유는 쓰지 않습니다 - 제목 없이 "지난번 만드신 와 비슷해요" 를 내지 않습니다.
    cooked_titles: tuple[str, ...] = ()
    #: 클러스터별 노출·반응 관측. 탐색 슬롯의 Thompson 이 씁니다 (`user_cluster_stat`).
    cluster_seen: Mapping[int, int] = field(default_factory=dict)
    cluster_hits: Mapping[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CorpusStats:
    """`feature_stats` 에서 읽는 코퍼스 통계. 없으면 그 피처는 측정 불가입니다."""

    flavor_mean: FlavorVector | None = None
    ingredient_idf: Mapping[int, float] = field(default_factory=dict)
    ingredient_names: Mapping[int, str] = field(default_factory=dict)
    #: 어느 μ 였는가. `recommendation_log.stats_version` 에 실립니다.
    #: μ 가 바뀌면 같은 레시피의 f_taste 가 바뀝니다.
    stats_version: int | None = None


#: `recipe_feature.difficulty` 는 1~5 이고 엔진의 `difficulty` 는 0~1 입니다.
DIFFICULTY_MIN, DIFFICULTY_MAX = 1, 5
#: 백엔드(`recipes.difficulty`)는 세 단계 열거형을 씁니다. 09-21 실데이터에서 이 값을
#: 숫자로 바꾸려다 `ValueError: could not convert string to float: 'EASY'` 로 터졌습니다.
#: 두 표현을 한 함수가 받습니다 — 변환이 두 곳에 있으면 한쪽만 고쳐집니다.
DIFFICULTY_WORDS = {"EASY": 0.0, "NORMAL": 0.5, "HARD": 1.0}
SEASON_MONTHS = 12


def _difficulty(value: object) -> float | None:
    """1~5 숫자이거나 EASY·NORMAL·HARD 이거나. 모르는 값은 None 입니다.

    모르는 값을 0 으로 두지 않습니다 — 0 은 "가장 쉬움" 이라 읽혀 `f_skill_fit` 이
    틀린 값으로 돕니다. 못 읽었으면 안 재는 것이 맞습니다.
    """
    if value is None:
        return None
    if isinstance(value, str):
        word = value.strip().upper()
        if word in DIFFICULTY_WORDS:
            return DIFFICULTY_WORDS[word]
        try:
            value = float(word)
        except ValueError:
            return None
    number = float(value)  # type: ignore[arg-type]
    if not DIFFICULTY_MIN <= number <= DIFFICULTY_MAX:
        return None
    return (number - DIFFICULTY_MIN) / (DIFFICULTY_MAX - DIFFICULTY_MIN)


def recipe_feature_from_row(row: Mapping[str, Any], *, month: int | None = None) -> RecipeFeature:
    """`recipe_feature` 한 행(+ `recipe.title`)을 엔진 모델로 바꿉니다. DB 를 모릅니다.

    저장소가 SQL 로 읽은 행이든 골든 픽스처의 행이든 같은 함수를 지나갑니다 — 두 경로가
    다른 변환을 하면 검사가 통과한 값과 서빙 값이 조용히 갈라집니다. 없는 칸은 None 으로
    두어 그 피처가 계산에서 빠지게 합니다(0 으로 메우지 않습니다 — 0 은 "계산했더니 0" 입니다).

    `season_vec` 은 달별 12칸이라 지금 달을 알아야 점수 하나가 됩니다. 달을 안 넘기면 None 입니다.
    """
    difficulty = row.get("difficulty")
    season_vec = row.get("season_vec")
    season_score: float | None = None
    if season_vec is not None and month is not None and len(season_vec) == SEASON_MONTHS:
        season_score = float(season_vec[month - 1])
    return RecipeFeature(
        recipe_id=int(row["recipe_id"]),
        title=str(row.get("title") or ""),
        essential_ids=frozenset(int(i) for i in (row.get("essential_ids") or ())),
        all_ids=frozenset(int(i) for i in (row.get("all_ids") or ())),
        flavor_vec=taste.as_vector(row.get("flavor_vec")),
        popularity_score=_optional_float(row.get("popularity_score")),
        quality_score=_optional_float(row.get("quality_score")),
        cook_minutes=None if row.get("cook_minutes") is None else int(row["cook_minutes"]),
        cuisine=normalize_cuisine(str(row.get("cuisine_family") or "")),
        dish_type=row.get("dish_type") or None,
        season_score=season_score,
        difficulty=_difficulty(difficulty),
    )


def _optional_float(value: float | int | str | None) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True)
class UserContext:
    """랭킹이 보는 사용자. 요청과 이력을 합친 것이며 엔진은 이것만 받습니다."""

    user_id: int
    #: 냉장고 전체 — 사용자가 넣은 것 ∪ 상비 재료(staple). ① 조회와 f_pantry_use 가 보는 집합입니다.
    pantry_ids: frozenset[int] = frozenset()
    #: 그중 **사용자가 직접 넣은 것.** None 이면 모른다는 뜻이고 그때는 `pantry_ids` 전부를
    #: 사용자 것으로 봅니다(검사·평가 도구처럼 staple 개념이 없는 호출자). 빈 집합은 "상비 재료
    #: 말고는 아무것도 없다" 는 뜻이라, 조회 계획이 처음부터 인기순으로 갑니다
    #: (아래 `has_own_ingredients`).
    own_pantry_ids: frozenset[int] | None = None
    expiring_ids: frozenset[int] = frozenset()
    taste_vec: FlavorVector = (None,) * taste.AXIS_COUNT
    max_cook_minutes: int | None = None
    #: 온보딩에서 고른 음식 유형 코드. `f_cuisine` 과 재정렬의 유형 슬롯이 봅니다.
    preferred_cuisines: frozenset[str] = frozenset()
    preferred_dish_types: frozenset[str] = frozenset()
    skill_level: float | None = None
    history: UserHistory = field(default_factory=UserHistory)
    #: 취향의 출처와 상태. 랭킹은 `taste_vec` 을 보고, 탐색 정책과 로그는 이것을 봅니다.
    #: 검사가 `UserContext` 를 직접 만들 때는 None 이며, 그때는 보통 사용자로 다룹니다.
    persona: Persona | None = None

    @property
    def has_own_ingredients(self) -> bool:
        """사용자가 넣은 재료가 하나라도 있는가.

        없으면 ① 이 재료 매칭으로 고를 것이 없습니다. 그런데도 조회하면 필수 재료가 아예 없는
        레시피(쌈장·초고추장 같은 양념 제조법)만 통과해 그것이 상위를 채웁니다 — 후보 수가
        완화 기준을 넘겨서 폴백도 안 걸립니다(09-18 실측 94건 ≥ 52). 그래서 이 값이 False 면
        조회 계획이 처음부터 인기순입니다(`candidate.first_plan`).
        """
        own = self.pantry_ids if self.own_pantry_ids is None else self.own_pantry_ids
        return bool(own)


#: `preferred_cuisines` 를 넘기지 않았다는 표시. 빈 목록("고른 유형이 없다")과 구분합니다.
_FROM_PERSONA: tuple[str, ...] = ("",)


def build_context(
    *,
    user_id: int,
    #: 기본값을 두지 않습니다. 빠뜨리면 조용히 취향 없는 사용자가 되어 목록이 통째로
    #: 달라지는데 에러는 나지 않습니다. 취향이 없으면 `persona.cold_persona()` 를 넘깁니다.
    persona: Persona,
    pantry_ids: Sequence[int] = (),
    own_pantry_ids: Sequence[int] | None = None,
    expiring_ids: Sequence[int] = (),
    history: UserHistory | None = None,
    max_cook_minutes: int | None = None,
    preferred_cuisines: Sequence[str] = _FROM_PERSONA,
    preferred_dish_types: Sequence[str] = (),
    skill_level: float | None = None,
) -> UserContext:
    """페르소나와 이력을 모아 랭킹이 쓸 문맥을 만듭니다.

    맛 취향은 `persona.vec` 그대로입니다. 값이 없는 축은 맛 계산에서 빠지고, 데이터가 오면
    코드를 고치지 않아도 켜집니다.

    음식 유형은 `user_preference.pref_cuisines` 를 읽는 쪽이 넘기고, 넘기지 않으면 페르소나에
    저장된 온보딩 응답을 씁니다. 빈 목록을 넘기는 것은 "고른 유형이 없다"는 뜻이라 페르소나로
    되돌아가지 않습니다 - 되돌아가면 유형을 지운 사용자에게 옛 선택이 되살아납니다.
    모르는 유형은 버립니다. 저장소가 라벨을 담고 있어도 랭킹은 코드로만 비교합니다.
    """
    past = history or UserHistory()
    given = persona.cuisines if preferred_cuisines is _FROM_PERSONA else preferred_cuisines
    cuisines = [code for raw in given if (code := normalize_cuisine(str(raw))) is not None]
    return UserContext(
        user_id=user_id,
        pantry_ids=frozenset(pantry_ids),
        own_pantry_ids=None if own_pantry_ids is None else frozenset(own_pantry_ids),
        expiring_ids=frozenset(expiring_ids),
        taste_vec=persona.vec,
        max_cook_minutes=max_cook_minutes,
        preferred_cuisines=frozenset(cuisines),
        preferred_dish_types=frozenset(preferred_dish_types),
        skill_level=skill_level,
        history=past,
        persona=persona,
    )
