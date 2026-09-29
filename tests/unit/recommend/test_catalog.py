"""백엔드 응답 → 엔진의 사전 (`engine/catalog.py`) 과 메모리 조회 (`engine/retrieval.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from features.recommend.backend_client import BackendIngredient, BackendRecipe
from features.recommend.engine import allergy, catalog, retrieval
from features.recommend.engine import candidate as plans
from features.recommend.engine.context import UserContext, build_context
from features.recommend.engine.persona import cold_persona
from features.recommend.policy import RankingPolicy

NOW = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)
SEED = catalog.load_seed()
#: 재료 이름은 AI 쪽 시드에 있는 것들입니다. 맛 · 상비 · 알레르기 군이 거기서 붙습니다.
NAMES = {1: "두부", 2: "새우", 3: "간장", 4: "소금", 5: "양파", 6: "우유", 7: "돼지고기", 8: "감자"}


def ingredients(**extra: dict[str, object]) -> list[BackendIngredient]:
    return [
        BackendIngredient(
            ingredient_id=key, name=name, default_shelf_life_days=7, **extra.get(name, {})
        )
        for key, name in NAMES.items()
    ]


def recipe(
    recipe_id: int, title: str, main: list[int], extra: tuple[int, ...] = (), **more: object
) -> BackendRecipe:
    items = [{"ingredient_id": i, "is_main": True} for i in main]
    items += [{"ingredient_id": i, "is_main": False} for i in extra]
    return BackendRecipe(recipe_id=recipe_id, title=title, ingredients=items, **more)


def built(
    recipes: list[BackendRecipe], items: list[BackendIngredient] | None = None
) -> catalog.Catalog:
    return catalog.build_catalog(items or ingredients(), recipes, SEED, NOW)


def context(
    own: list[int], cat: catalog.Catalog, user_id: int = 1, max_minutes: int | None = None
) -> UserContext:
    return build_context(
        user_id=user_id,
        persona=cold_persona(),
        pantry_ids=sorted(set(own) | cat.staple_ids),
        own_pantry_ids=own,
        max_cook_minutes=max_minutes,
    )


def fetch(
    cat: catalog.Catalog,
    own: list[int],
    labels: list[str] | None = None,
    max_minutes: int | None = None,
) -> retrieval.Retrieved:
    resolution = allergy.resolve(labels or [], cat.corpus.ingredient_names, cat.allergen_groups)
    ctx = context(own, cat, max_minutes=max_minutes)
    return retrieval.retrieve(cat, ctx, resolution, RankingPolicy(), 20, 0.2)


def plenty(start: int = 100) -> list[BackendRecipe]:
    """첫 조회로 후보가 차게 하는 두부 레시피들. 모자라면 사다리가 k 를 풀어 다른 것을 봅니다."""
    return [recipe(i, f"두부요리{i}", [1]) for i in range(start, start + 60)]


# ── 사전 만들기 ──────────────────────────────────────────────────
def test_unpublished_and_mainless_recipes_are_not_served() -> None:
    """필수 재료가 없는 레시피는 부족을 셀 수 없어 어느 냉장고에서나 "전부 갖춤" 이 됩니다."""
    cat = built(
        [
            recipe(1, "두부조림", [1], (3,)),
            recipe(2, "지워진 레시피", [1], is_published=False),
            recipe(3, "양념장", [], (3, 4)),
        ]
    )

    assert set(cat.recipes) == {1}
    assert cat.recipes[1].essential_ids == frozenset({1})
    assert cat.recipes[1].all_ids == frozenset({1, 3})


def test_the_backend_vocabulary_is_read_into_the_engine_vocabulary() -> None:
    cat = built(
        [
            recipe(1, "팟타이", [2], cuisine_type="ETC", difficulty="HARD", cooking_time=25),
            recipe(2, "두부조림", [1], cuisine_type="KOREAN", difficulty="EASY"),
        ]
    )

    assert cat.recipes[1].cuisine == "asian_other"
    assert cat.recipes[1].difficulty == 1.0
    assert cat.recipes[1].cook_minutes == 25
    assert cat.recipes[2].cuisine == "korean"


def test_flavour_is_rebuilt_from_the_ingredients() -> None:
    """백엔드에는 맛 정보가 없습니다. 재료에서 계산합니다."""
    cat = built([recipe(1, "두부조림", [1], (3,)), recipe(2, "새우볶음", [2], (5,))])

    assert all(value is not None for value in cat.recipes[1].flavor_vec)
    assert cat.recipes[1].flavor_vec != cat.recipes[2].flavor_vec
    assert cat.corpus.flavor_mean is not None
    assert cat.corpus.ingredient_names[1] == "두부"


def test_popularity_without_any_variation_is_missing_not_a_half() -> None:
    """서비스 초기에는 스크랩이 전부 0 입니다. 0.5 로 메우면 꺼진 신호가 켜진 것처럼 보입니다."""
    flat = built([recipe(i, f"요리{i}", [1], popularity={"scrap_count": 0}) for i in (1, 2, 3)])
    spread = built(
        [
            recipe(i, f"요리{i}", [1], popularity={"scrap_count": n})
            for i, n in ((1, 0), (2, 5), (3, 90))
        ]
    )

    assert all(r.popularity_score is None for r in flat.recipes.values())
    scores = {i: spread.recipes[i].popularity_score for i in (1, 2, 3)}
    assert scores == {1: 0.0, 2: 0.5, 3: 1.0}


def test_tied_counts_share_a_percentile_and_signals_are_averaged() -> None:
    cat = built(
        [
            recipe(1, "가", [1], popularity={"scrap_count": 3, "view_count": 10}),
            recipe(2, "나", [1], popularity={"scrap_count": 3, "view_count": 500}),
            recipe(3, "다", [1], popularity={"scrap_count": 9, "view_count": 10}),
        ]
    )

    assert cat.recipes[1].popularity_score == pytest.approx(0.25)
    assert cat.recipes[2].popularity_score == pytest.approx((0.25 + 1.0) / 2)
    assert cat.recipes[3].popularity_score == pytest.approx((1.0 + 0.25) / 2)


def test_a_single_rating_is_not_quality() -> None:
    cat = built(
        [
            recipe(1, "가", [1], rating={"average": 5.0, "count": 1}),
            recipe(2, "나", [1], rating={"average": 4.0, "count": 12}),
        ]
    )

    assert cat.recipes[1].quality_score is None
    assert cat.recipes[2].quality_score == pytest.approx(0.8)


def test_staples_come_from_the_backend_once_it_sends_them() -> None:
    from_seed = built([recipe(1, "두부조림", [1])])
    from_backend = built(
        [recipe(1, "두부조림", [1])],
        ingredients(양파={"is_staple": True}, 소금={"is_staple": False}),
    )

    # 빈도로 짐작하지 않습니다 — 양파는 흔하지만 사용자가 사야 하는 재료입니다.
    assert {NAMES[i] for i in from_seed.staple_ids} == {"간장", "소금"}
    assert {NAMES[i] for i in from_backend.staple_ids} == {"양파"}


def test_the_backend_allergen_array_carries_more_than_one_group() -> None:
    """간장은 대두이면서 밀입니다. AI 쪽 시드는 재료마다 군을 하나만 답니다."""
    seed_only = built([recipe(1, "두부조림", [1], (3,))])
    with_array = built(
        [recipe(1, "두부조림", [1], (3,))], ingredients(간장={"allergens": ["대두", "밀"]})
    )

    assert seed_only.allergen_groups[3] == ("soy",)
    assert with_array.allergen_groups[3] == ("gluten", "soy")
    assert [c.recipe_id for c in fetch(seed_only, [1], ["밀"]).candidates] == [1]
    assert fetch(with_array, [1], ["밀"]).candidates == []


def test_an_ingredient_the_seed_does_not_know_is_reported() -> None:
    items = [*ingredients(), BackendIngredient(ingredient_id=99, name="처음보는재료")]

    assert built([recipe(1, "두부조림", [1])], items).unmapped_ingredients == ("처음보는재료",)


# ── 조회 ─────────────────────────────────────────────────────────
def test_a_recipe_needs_an_overlap_and_few_enough_missing() -> None:
    cat = built(
        [
            recipe(1, "두부조림", [1]),
            recipe(2, "두부새우볶음", [1, 2]),
            recipe(3, "돼지고기감자조림", [7, 8]),
            recipe(4, "두부돼지감자새우우유찜", [1, 2, 6, 7, 8]),
            *plenty(),
        ]
    )

    found = fetch(cat, [1])

    got = {c.recipe_id: c for c in found.candidates}
    assert found.stage == plans.FALLBACK_NONE
    assert {1, 2} <= set(got) and not {3, 4} & set(got)
    assert got[1].coverage == 1.0 and got[1].missing_count == 0
    assert got[2].missing_ids == [2]
    assert found.filters["no_overlap"] == 1
    assert found.filters["missing_gt_k"] == 1
    # 다 갖춘 것이 부족한 것보다 앞입니다.
    assert found.candidates[-1].recipe_id == 2


def test_too_few_candidates_loosen_the_search_step_by_step() -> None:
    """후보가 모자라면 부족 허용을 풀고, 그래도 모자라면 인기순입니다 (`candidate.py` 의 사다리)."""
    cat = built([recipe(1, "두부조림", [1]), recipe(4, "두부돼지감자새우찜", [1, 2, 7, 8])])

    found = fetch(cat, [1])

    assert found.stage == plans.FALLBACK_POPULARITY
    assert [c.recipe_id for c in found.candidates] == [1, 4]


def test_cooking_time_is_a_hard_limit() -> None:
    cat = built(
        [
            recipe(1, "빠른두부", [1], cooking_time=10),
            recipe(2, "오랜두부", [1], cooking_time=90),
            *plenty(),
        ]
    )

    found = fetch(cat, [1], max_minutes=30)

    assert 1 in {c.recipe_id for c in found.candidates}
    assert 2 not in {c.recipe_id for c in found.candidates}
    assert found.filters["cooktime_cut"] == 1


def test_allergies_are_cut_here_by_ingredient_and_by_title() -> None:
    """09-21 실데이터 — 「고추장 건새우볶음」 의 재료 행에 건새우가 없었습니다."""
    cat = built(
        [
            recipe(1, "두부조림", [1]),
            recipe(2, "두부새우볶음", [1], (2,)),
            recipe(3, "두부 건새우볶음", [1]),
        ]
    )

    found = fetch(cat, [1], ["새우"])

    assert [c.recipe_id for c in found.candidates] == [1]
    assert found.filters["allergy_cut"] == 2


def test_a_bare_fridge_goes_straight_to_popularity() -> None:
    cat = built(
        [
            recipe(1, "두부조림", [1], popularity={"scrap_count": 1}),
            recipe(2, "새우볶음", [2], popularity={"scrap_count": 50}),
        ]
    )

    found = fetch(cat, [])

    assert found.stage == plans.FALLBACK_POPULARITY
    assert [c.recipe_id for c in found.candidates] == [2, 1]


def test_the_cut_is_not_decided_by_the_recipe_number() -> None:
    """충족률이 같은 후보가 상한보다 많으면 번호가 낮은 레시피만 영원히 후보가 됩니다."""
    cat = built([recipe(i, f"두부요리{i}", [1]) for i in range(1, 61)])
    policy = RankingPolicy(candidate_limit=20, mmr_pool_size=20, explore_pool_size=20)

    def cut(user_id: int) -> list[int]:
        resolution = allergy.resolve([], cat.corpus.ingredient_names, cat.allergen_groups)
        got = retrieval.retrieve(cat, context([1], cat, user_id), resolution, policy, 5, 0.2)
        return [c.recipe_id for c in got.candidates]

    assert len(cut(1)) == 20
    assert cut(1) == cut(1)
    assert cut(1) != cut(2)
    assert cut(1) != sorted(cut(1))
