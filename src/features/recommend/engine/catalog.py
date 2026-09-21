"""백엔드에서 받은 레시피 · 재료를 엔진이 쓰는 모양으로 바꿔 메모리에 둡니다.

레시피 전량을 엔진 모델로 바꾸면 4.2MB 입니다(2026-09-21 표본 21,491건). 추천 한 번이 후보
500건을 보므로 요청마다 API 로 받으면 왕복마다 수백 KB 가 오갑니다. 그래서 하루 한 번 받아
통째로 들고 있습니다. 바꾸는 일은 여기서 하고, 받는 일은 `backend_client.py` 가 합니다.

백엔드가 주지 않는 것은 AI 쪽에서 만듭니다.

    맛 6축        재료에서 계산합니다. 데이터 파트의 맛 시드와 산출 규칙(`ingest/flavor.py`)을
                  그대로 씁니다 — 표본에서 복원한 평균이 원래 값과 0.023 안에서 일치했습니다.
    인기 점수     스크랩 · 조회 · 주문 수의 백분위입니다. 값이 전부 같으면(서비스 초기의 전부 0)
                  정보가 없는 것이라 None 입니다. 0.5 로 메우면 꺼진 신호가 켜진 것처럼 보입니다.
    재료 희소도   레시피에 나온 횟수의 역수(IDF)입니다.
    상비 재료     백엔드의 `is_staple` 이 있으면 그것, 없으면 AI 쪽 시드를 이름으로 잇습니다.
    알레르기 군   백엔드의 `allergens` 배열이 있으면 그것, 없으면 AI 쪽 시드입니다.

주의: 없는 값은 None 입니다. 0 으로 메우지 않습니다 — 0 은 "계산했더니 0" 입니다.
주의: 공개되지 않은 레시피(`is_published=false`)와 필수 재료가 하나도 없는 레시피는 싣지 않습니다.
   뒤의 것은 부족 재료를 셀 수 없어 어느 냉장고에서나 "전부 갖춤" 이 됩니다.
"""

from __future__ import annotations

import csv
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from features.recommend.backend_client import (
    BackendIngredient,
    BackendRecipe,
    BackendRecipeIngredient,
)
from features.recommend.engine import allergy, dish, taste
from features.recommend.engine.context import CorpusStats, RecipeFeature, recipe_feature_from_row
from features.recommend.enums import IngredientRole, normalize_allergen
from features.recommend.ingest.flavor import (
    DEFAULT_TIER,
    POSITION_BOOST,
    TITLE_BOOST,
    UNIT_TIER,
    FlavorTable,
    aggregate,
    in_title,
)

SEEDS = Path(__file__).resolve().parents[4] / "seeds"
#: 양이 정해지지 않은 단위. 제목이나 자리로 세기를 올리지 않습니다(`ingest/flavor.py` 와 같은 규칙).
_VAGUE_UNITS = frozenset({"약간", "적당량", "조금"})
#: 주재료를 앞에 적는 관행을 신호로 쓰는 최소 재료 수.
_POSITION_MIN_ITEMS = 6
#: 평점이 이보다 적으면 품질 신호로 쓰지 않습니다. 한 명의 5점은 품질이 아닙니다.
_MIN_RATINGS = 3
_MAX_RATING = 5.0


@dataclass(frozen=True)
class Catalog:
    """서빙이 읽는 사전 한 벌. 통째로 갈아 끼우므로 읽는 쪽은 잠금이 필요 없습니다."""

    recipes: Mapping[int, RecipeFeature]
    corpus: CorpusStats
    staple_ids: frozenset[int]
    allergen_groups: Mapping[int, tuple[str, ...]]
    shelf_life_days: Mapping[int, int]
    #: 재료 → 그 재료가 필수인 레시피. ① 조회가 2만 건을 다 훑지 않게 합니다.
    by_essential: Mapping[int, tuple[int, ...]]
    synced_at: datetime
    #: 이 사전이 어느 동기화의 것인가. 추적의 `feature_version` 에 실립니다.
    version: str
    #: 시드에 닿지 못해 맛도 알레르기 군도 모르는 재료. 비어 있지 않으면 하드컷에 구멍이 있습니다.
    unmapped_ingredients: tuple[str, ...] = ()
    stats: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _Seed:
    flavors: FlavorTable
    rows: Mapping[str, Mapping[str, str]]

    def row(self, name: str) -> Mapping[str, str] | None:
        return self.rows.get(name) or self.rows.get(allergy.BACKEND_NAME_ALIASES.get(name, ""))


def load_seed(seeds: Path = SEEDS) -> _Seed:
    """AI 쪽 재료 시드. 파일을 읽으므로 동기화마다가 아니라 한 번만 부릅니다."""
    with (seeds / "ingredient.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["name"]: row for row in csv.DictReader(handle)}
    return _Seed(flavors=FlavorTable.from_seeds(seeds), rows=rows)


def build_catalog(
    ingredients: Sequence[BackendIngredient],
    recipes: Iterable[BackendRecipe],
    seed: _Seed,
    now: datetime,
) -> Catalog:
    """받은 것 전부 → 사전 한 벌. 순수 함수입니다(시드는 미리 읽어 넘깁니다)."""
    names = {item.ingredient_id: item.name for item in ingredients}
    served = [r for r in recipes if r.is_published and any(i.is_main for i in r.ingredients)]
    flavor_of = {item.ingredient_id: _ingredient_flavor(item.name, seed) for item in ingredients}
    seasoning = {
        item.ingredient_id for item in ingredients if _flag(seed.row(item.name), "is_seasoning")
    }
    popularity = _popularity_scores(served)

    features: dict[int, RecipeFeature] = {}
    vectors: list[list[float]] = []
    usage: Counter[int] = Counter()
    by_essential: dict[int, list[int]] = defaultdict(list)
    for recipe in served:
        vector = _recipe_flavor(recipe, names, flavor_of, seasoning)
        vectors.append(vector)
        essential = [i.ingredient_id for i in recipe.ingredients if i.is_main]
        every = [i.ingredient_id for i in recipe.ingredients]
        features[recipe.recipe_id] = recipe_feature_from_row(
            {
                "recipe_id": recipe.recipe_id,
                "title": recipe.title,
                "essential_ids": essential,
                "all_ids": every,
                "flavor_vec": vector,
                "popularity_score": popularity.get(recipe.recipe_id),
                "quality_score": _quality(recipe),
                "cook_minutes": recipe.cooking_time,
                "cuisine_family": recipe.cuisine_type,
                "difficulty": recipe.difficulty,
            }
        )
        usage.update(set(every))
        for ingredient_id in set(essential):
            by_essential[ingredient_id].append(recipe.recipe_id)

    total = len(features)
    mains = dish.main_words(tuple(sorted(names.values())))
    for feature in features.values():
        # 요리 이름을 미리 뽑아 둡니다. 안 그러면 제목을 처음 보는 첫 요청이 그 값을 다 냅니다.
        dish.dish_name(feature.title, mains)

    groups, unmapped = _allergen_groups(ingredients, names, seed)
    return Catalog(
        recipes=features,
        corpus=CorpusStats(
            flavor_mean=taste.as_vector(_mean_vector(vectors)),
            ingredient_idf={i: math.log(total / count) for i, count in usage.items() if count},
            ingredient_names=names,
            stats_version=None,
        ),
        staple_ids=_staples(ingredients, seed),
        allergen_groups=groups,
        shelf_life_days={
            item.ingredient_id: item.default_shelf_life_days
            for item in ingredients
            if item.default_shelf_life_days is not None and item.default_shelf_life_days > 0
        },
        by_essential={key: tuple(value) for key, value in by_essential.items()},
        synced_at=now,
        version=f"backend-{now:%Y%m%dT%H%M%S}-{total}",
        unmapped_ingredients=unmapped,
        stats={
            "ingredients": len(names),
            "recipes_received": len(served),
            "recipes_served": total,
            "recipes_with_popularity": len(popularity),
        },
    )


def _ingredient_flavor(name: str, seed: _Seed) -> list[float]:
    """재료 하나의 6축 기여. 시드에 없으면 0벡터입니다 — 맛에 기여하지 않는 것으로 봅니다."""
    row = seed.row(name)
    return seed.flavors.of(row["name"] if row else name, row["category_path"] if row else None)


def _recipe_flavor(
    recipe: BackendRecipe,
    names: Mapping[int, str],
    flavor_of: Mapping[int, list[float]],
    seasoning: set[int],
) -> list[float]:
    """레시피 하나의 6축. 세기는 단위 · 제목 · 자리로 정합니다(`ingest/flavor.py` 와 같은 규칙)."""
    count = len(recipe.ingredients)
    scaled: list[tuple[list[float], IngredientRole | None]] = []
    for position, item in enumerate(recipe.ingredients):
        name = names.get(item.ingredient_id) or item.name or ""
        unit = item.unit or ""
        boost = 1.0
        if name and in_title(name, recipe.title):
            boost *= TITLE_BOOST
        if count >= _POSITION_MIN_ITEMS and position < max(1, count // 3):
            boost *= POSITION_BOOST
        if unit in _VAGUE_UNITS:
            boost = min(boost, 1.0)
        strength = UNIT_TIER.get(unit, DEFAULT_TIER) * boost
        base = flavor_of.get(item.ingredient_id, [0.0] * taste.AXIS_COUNT)
        scaled.append(([min(1.0, value * strength) for value in base], _role(item, seasoning)))
    return aggregate(scaled, mode="role_w")


def _role(item: BackendRecipeIngredient, seasoning: set[int]) -> IngredientRole:
    """양념인지는 AI 쪽 시드가 정하고, 필수인지는 백엔드의 `is_main` 이 정합니다."""
    if item.ingredient_id in seasoning:
        return IngredientRole.SEASONING
    return IngredientRole.ESSENTIAL if item.is_main else IngredientRole.OPTIONAL


def _popularity_scores(recipes: Sequence[BackendRecipe]) -> dict[int, float]:
    """스크랩 · 조회 · 주문 수 → 0~1. 신호마다 백분위를 내고 있는 것끼리 평균합니다.

    한 신호의 값이 전부 같으면 그 신호는 뺍니다. 서비스 초기에는 전부 0 이라, 그대로 백분위를
    내면 전원이 0.5 를 받아 인기 신호가 켜진 것처럼 보이면서 순위에는 아무 일도 하지 않습니다.
    """
    per_signal: list[dict[int, float]] = []
    for signal in ("scrap_count", "view_count", "order_count"):
        raw = {
            r.recipe_id: float(value)
            for r in recipes
            if r.popularity is not None
            and (value := getattr(r.popularity, signal)) is not None
            and value >= 0
        }
        if len(set(raw.values())) > 1:
            per_signal.append(_percentiles({key: math.log1p(v) for key, v in raw.items()}))
    scores: dict[int, float] = {}
    for recipe_id in {key for ranks in per_signal for key in ranks}:
        known = [ranks[recipe_id] for ranks in per_signal if recipe_id in ranks]
        scores[recipe_id] = round(statistics.fmean(known), 6)
    return scores


def _percentiles(values: Mapping[int, float]) -> dict[int, float]:
    """값 → 백분위(0~1). 같은 값은 같은 백분위를 받습니다(평균 순위)."""
    ordered = sorted(values.items(), key=lambda pair: pair[1])
    last = len(ordered) - 1
    out: dict[int, float] = {}
    start = 0
    while start <= last:
        end = start
        while end < last and ordered[end + 1][1] == ordered[start][1]:
            end += 1
        rank = (start + end) / 2 / last if last else 0.5
        for key, _value in ordered[start : end + 1]:
            out[key] = rank
        start = end + 1
    return out


def _quality(recipe: BackendRecipe) -> float | None:
    rating = recipe.rating
    if rating is None or rating.average is None or (rating.count or 0) < _MIN_RATINGS:
        return None
    return max(0.0, min(1.0, rating.average / _MAX_RATING))


def _mean_vector(vectors: Sequence[Sequence[float]]) -> list[float] | None:
    if not vectors:
        return None
    return [statistics.fmean(v[axis] for v in vectors) for axis in range(taste.AXIS_COUNT)]


def _staples(ingredients: Sequence[BackendIngredient], seed: _Seed) -> frozenset[int]:
    """백엔드가 `is_staple` 을 한 건이라도 주면 그쪽이 정본입니다. 아니면 AI 쪽 시드입니다.

    빈도로 짐작하지 않습니다. 마늘 · 양파 · 대파는 레시피의 30% 에 나오지만 상비 재료가 아니라
    사용자가 사야 하는 재료입니다. 상비로 치면 없는 재료를 있다고 보고 후보를 뽑습니다.
    """
    if any(item.is_staple is not None for item in ingredients):
        return frozenset(item.ingredient_id for item in ingredients if item.is_staple)
    return frozenset(
        item.ingredient_id for item in ingredients if _flag(seed.row(item.name), "is_staple")
    )


def _allergen_groups(
    ingredients: Sequence[BackendIngredient], names: Mapping[int, str], seed: _Seed
) -> tuple[dict[int, tuple[str, ...]], tuple[str, ...]]:
    """재료 → 알레르기 군들, 그리고 어느 쪽으로도 군을 알 수 없던 재료 이름.

    재료마다 정합니다. 백엔드가 그 재료의 `allergens` 를 줬으면(빈 배열 포함) 그것이 정본이고,
    안 줬으면(None) AI 쪽 시드입니다. 백엔드가 일부 재료만 채운 동안에도 나머지가 비지 않습니다.
    """
    seed_groups = {name: row.get("allergen_group", "").strip() for name, row in seed.rows.items()}
    from_seed, unmapped = allergy.ingredient_groups(names, seed_groups)
    groups: dict[int, tuple[str, ...]] = {}
    for item in ingredients:
        if item.allergens is not None:
            codes = [normalize_allergen(label) for label in item.allergens]
            groups[item.ingredient_id] = tuple(sorted({code for code in codes if code}))
        elif from_seed.get(item.ingredient_id):
            groups[item.ingredient_id] = (from_seed[item.ingredient_id],)
    given = {item.name for item in ingredients if item.allergens is not None}
    return groups, tuple(name for name in unmapped if name not in given)


def _flag(row: Mapping[str, str] | None, column: str) -> bool:
    return bool(row) and (row or {}).get(column, "").strip().lower() == "true"
