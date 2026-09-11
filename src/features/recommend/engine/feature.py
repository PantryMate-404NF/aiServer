"""17개 랭킹 피처의 원값 계산. 순수 함수만 둡니다.

`enums.FEATURE_KEYS` 전부를 채웁니다. 계산할 수 없는 것은 **0.0 이 아니라 None** 입니다.
0.0 은 "계산했더니 0", None 은 "계산할 수 없음"이고 학습에서 완전히 다른 뜻입니다
(`stage.ScoredCandidate` 주석). 가중합은 None 을 분자·분모에서 함께 빼므로, 데이터가
없는 피처의 가중치를 0 으로 내릴 필요가 없습니다.

무엇이 None 이 되는가는 두 갈래입니다.

  수단이 없음   `enums.UNAVAILABLE_FEATURES` — 항상 None 입니다.
  데이터가 없음 `recipe_feature` 의 해당 칸이 비어 있을 때만 None 입니다. 값이 오면
                코드를 고치지 않아도 켜집니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from features.recommend.engine import taste
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.enums import FEATURE_KEYS, UNAVAILABLE_FEATURES
from features.recommend.stage import Candidate

#: 코퍼스 통계에 없는 재료의 IDF. 1 이면 가중치 없는 자카드와 같습니다.
DEFAULT_IDF = 1.0


def compute_features(
    candidate: Candidate,
    recipe: RecipeFeature,
    ctx: UserContext,
    corpus: CorpusStats,
    *,
    max_missing: int,
    taste_min_norm: float = 0.0,
) -> dict[str, float | None]:
    """피처 17종의 원값. `FEATURE_KEYS` 를 하나도 빠뜨리지 않습니다."""
    values: dict[str, float | None] = {
        # ── A군: 재료 매칭 ────────────────────────────────────
        "f_coverage": _clamp(candidate.coverage),
        "f_missing": _missing(candidate.missing_count, max_missing),
        "f_expiring": _ratio(recipe.essential_ids & ctx.expiring_ids, ctx.expiring_ids),
        "f_pantry_use": _ratio(recipe.all_ids & ctx.pantry_ids, ctx.pantry_ids),
        # ── B군: 유저 선호 ────────────────────────────────────
        "f_taste": taste.centered_cosine(
            ctx.taste_vec, recipe.flavor_vec, corpus.flavor_mean, taste_min_norm
        ),
        "f_ing_pref": _preference(recipe.all_ids, ctx.history.liked_ingredient_ids),
        "f_cuisine": _membership(recipe.cuisine, ctx.preferred_cuisines),
        "f_dish_type": _membership(recipe.dish_type, ctx.preferred_dish_types),
        "f_cooccur": cooccurrence(
            recipe.all_ids, ctx.history.cooked_ingredient_sets, corpus.ingredient_idf
        ),
        # ── C군: 품질 ────────────────────────────────────────
        "f_popularity": _optional(recipe.popularity_score),
        "f_quality": _optional(recipe.quality_score),
        # ── D군: 컨텍스트 ────────────────────────────────────
        "f_time_fit": time_fit(recipe.cook_minutes, ctx.max_cook_minutes),
        "f_season": _optional(recipe.season_score),
        "f_skill_fit": skill_fit(recipe.difficulty, ctx.skill_level),
    }
    # 수단 자체가 없는 피처. 데이터가 와도 계산 코드가 없으므로 항상 None 입니다.
    for key in UNAVAILABLE_FEATURES:
        values[key] = None
    missing = set(FEATURE_KEYS) - set(values)
    if missing:
        raise ValueError(f"계산하지 않은 피처가 있습니다: {sorted(missing)}")
    return values


def cooccurrence(
    all_ids: frozenset[int],
    cooked_sets: Sequence[frozenset[int]],
    idf: Mapping[int, float],
) -> float | None:
    """최근 조리한 레시피들과의 IDF 가중 자카드 최대값. 이력이 없으면 측정 불가입니다."""
    if not cooked_sets or not all_ids:
        return None
    return max(jaccard_idf(all_ids, past, idf) for past in cooked_sets)


def jaccard_idf(left: frozenset[int], right: frozenset[int], idf: Mapping[int, float]) -> float:
    """IDF 로 가중한 자카드 유사도. 흔한 재료(소금, 물)가 겹친다고 비슷하다고 보지 않습니다."""
    union = left | right
    if not union:
        return 0.0
    shared = sum(idf.get(i, DEFAULT_IDF) for i in left & right)
    return shared / sum(idf.get(i, DEFAULT_IDF) for i in union)


def time_fit(cook_minutes: int | None, max_minutes: int | None) -> float | None:
    """조리시간 적합도. 상한이나 조리시간이 없으면 측정 불가입니다."""
    if cook_minutes is None or max_minutes is None or max_minutes <= 0:
        return None
    return _clamp(1.0 - max(0.0, (cook_minutes - max_minutes) / max_minutes))


def skill_fit(difficulty: float | None, skill_level: float | None) -> float | None:
    """난이도가 실력에 가까울수록 1 입니다. 둘 중 하나라도 없으면 측정 불가입니다."""
    if difficulty is None or skill_level is None:
        return None
    return _clamp(1.0 - abs(difficulty - skill_level))


def _missing(missing_count: int, max_missing: int) -> float:
    """부족 재료가 적을수록 1 에 가깝습니다. 상한을 0 으로 준 요청도 나눗셈이 되게 합니다."""
    return _clamp(1.0 - missing_count / (max_missing + 1))


def _ratio(hit: frozenset[int], whole: frozenset[int]) -> float | None:
    """분모가 비어 있으면 잴 대상이 없다는 뜻이라 측정 불가입니다."""
    if not whole:
        return None
    return _clamp(len(hit) / len(whole))


def _preference(all_ids: frozenset[int], liked: frozenset[int]) -> float | None:
    """선호 재료가 하나도 없으면 0 이 아니라 측정 불가입니다.

    0 은 "좋아하는 재료가 안 들어갔다"이고 None 은 "무엇을 좋아하는지 모른다"입니다.
    콜드 사용자 전원에게 0 을 주면 그 피처가 모두를 똑같이 깎아 아무 정보도 없이
    분모만 차지합니다.
    """
    if not liked or not all_ids:
        return None
    return _clamp(len(all_ids & liked) / len(all_ids))


def _membership(value: str | None, preferred: frozenset[str]) -> float | None:
    """선호 목록에 있으면 1, 없으면 0. 값이나 선호가 비어 있으면 측정 불가입니다."""
    if value is None or not preferred:
        return None
    return 1.0 if value in preferred else 0.0


def _optional(value: float | None) -> float | None:
    return None if value is None else _clamp(value)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
