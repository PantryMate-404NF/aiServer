"""같은 요리의 판본을 한 목록에 하나만 (`engine/dish.py`). 제목은 백엔드 실데이터의 것입니다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from features.recommend import service
from features.recommend.engine import dish
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.enums import FEATURE_KEYS
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate, ScoredCandidate

NAMES = (
    "두부",
    "감자",
    "양파",
    "대파",
    "김치",
    "배추김치",
    "돼지고기",
    "참치",
    "새우",
    "건새우",
    "달걀",
    "콩나물",
    "표고버섯",
    "버섯",
)
MAINS = dish.main_words(NAMES)


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("매콤 두부조림", "두부조림"),
        ("두부 조림 만들기", "두부조림"),
        ("백종원두부조림 황금레시피", "두부조림"),
        ("간단한밑반찬 알감자조림", "감자조림"),
        ("돼지고기김치찌개", "김치찌개"),
        ("참치김치찌개 끓이기", "김치찌개"),
        ("참치 김치볶음밥 만드는법 플레이팅도 최고", "김치볶음밥"),
        ("양파 장아찌", "양파장아찌"),
        ("양파장아찌 아주 쉽고 맛있게 담그기", "양파장아찌"),
        ("햇양파 무침", "양파무침"),
        ("항금알이 내 밥상에 등장?!♥ 카레달걀장조림", "달걀장조림"),
        ("한 끼 식사로 좋은 표고버섯덮밥 만들기", "표고버섯덮밥"),
        ("하트 계란말이 예쁘게 만드는법 레시피", "계란말이"),
        ("떡국", "떡국"),
    ],
)
def test_the_dish_name_is_read_out_of_a_blog_style_title(title: str, name: str) -> None:
    assert dish.dish_name(title, MAINS) == name


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("입맛없는 딸냄을 위해 만든 닭볶음탕..", "닭곰탕"),
        ("밥도둑 새우호박볶음", "고추장 건새우볶음 : 밑반찬 하나로 공기밥 뚝딱"),
        ("초간단 감자채볶음 만들기", "부서지지않은 감자조림"),
        ("파채무침 만드는법", "양파무침"),
    ],
)
def test_different_dishes_keep_different_names(one: str, other: str) -> None:
    """잘못 묶으면 다른 요리가 가려집니다. 못 묶는 것보다 나쁩니다."""
    first, second = dish.dish_name(one, MAINS), dish.dish_name(other, MAINS)

    assert first is not None and second is not None
    assert first != second


@pytest.mark.parametrize(
    "title",
    ["오늘의 집밥", "치킨 양념만들기", "소시지 도리아", "콩나물", "배추김치", "삼계탕", ""],
)
def test_a_title_without_a_dish_is_left_alone(title: str) -> None:
    """재료 이름 그 자체, 주재료 없는 한 글자 조리법(집밥·공기밥)은 요리 이름이 아닙니다."""
    assert dish.dish_name(title, MAINS) is None


def test_a_trailing_common_word_does_not_beat_the_dish() -> None:
    """09-21 실측 — 뒤에서부터만 보면 `공기밥` 이 요리 이름이 됩니다."""
    title = "고추장 건새우볶음 : 밑반찬 하나로 공기밥 뚝딱"

    assert dish.dish_name(title, MAINS) == "건새우볶음"


def _scored(recipe_id: int, score: float) -> ScoredCandidate:
    return ScoredCandidate(
        recipe_id=recipe_id,
        missing_count=0,
        missing_ids=[],
        coverage=1.0,
        score=score,
        features=dict.fromkeys(FEATURE_KEYS),
    )


def _names() -> dict[int, str]:
    return dict(enumerate(NAMES, start=1))


def test_the_best_ranked_version_is_the_one_that_stays(
    make_recipe: Callable[..., RecipeFeature],
) -> None:
    recipes = {
        1: make_recipe(1, [1], title="매콤 두부조림"),
        2: make_recipe(2, [2], title="부서지지않은 감자조림"),
        3: make_recipe(3, [1], title="두부 조림 만들기"),
        4: make_recipe(4, [1, 3], title="백종원두부조림 황금레시피"),
        5: make_recipe(5, [2], title="감자볶음 황금레시피"),
    }
    ranked = [_scored(i, 1.0 - i * 0.1) for i in (1, 2, 3, 4, 5)]

    kept = dish.collapse_versions(ranked, recipes, _names(), 1)

    assert [item.recipe_id for item in kept] == [1, 2, 5]


def test_the_same_title_is_one_dish_even_without_a_dish_name(
    make_recipe: Callable[..., RecipeFeature],
) -> None:
    """백엔드 표본에서 제목이 완전히 같은 레시피가 782건이었습니다."""
    recipes = {
        1: make_recipe(1, [1], title="소시지 도리아"),
        2: make_recipe(2, [2], title="[소시지]  도리아!!"),
        3: make_recipe(3, [3], title="새우 도리아"),
    }

    kept = dish.collapse_versions([_scored(i, 0.9) for i in (1, 2, 3)], recipes, _names(), 1)

    assert [item.recipe_id for item in kept] == [1, 3]


def test_the_same_whole_ingredient_set_counts_only_from_four(
    make_recipe: Callable[..., RecipeFeature],
) -> None:
    """두세 가지는 다른 요리도 같을 수 있습니다 — 달걀과 소금은 계란찜이고 삶은 달걀입니다."""
    recipes = {
        1: make_recipe(1, [1, 2], [3, 4], title="가"),
        2: make_recipe(2, [1, 2], [3, 4], title="나"),
        3: make_recipe(3, [11], [12], title="다"),
        4: make_recipe(4, [11], [12], title="라"),
    }

    kept = dish.collapse_versions([_scored(i, 0.9) for i in (1, 2, 3, 4)], recipes, _names(), 1)

    assert [item.recipe_id for item in kept] == [1, 3, 4]


def test_the_same_essential_ingredients_are_not_a_reason(
    make_recipe: Callable[..., RecipeFeature],
) -> None:
    """09-21 실측 — 이 기준으로 묶으면 달걀장조림 · 계란말이 · 계란찜이 한 요리가 됩니다."""
    recipes = {
        1: make_recipe(1, [11], [1], title="달걀장조림 구운계란으로 간단하게"),
        2: make_recipe(2, [11], [2], title="하트 계란말이 예쁘게 만드는법 레시피"),
        3: make_recipe(3, [11], [3], title="초간단 계란찜 전자렌지로"),
    }

    kept = dish.collapse_versions([_scored(i, 0.9) for i in (1, 2, 3)], recipes, _names(), 1)

    assert len(kept) == 3


def test_the_limit_is_a_knob(make_recipe: Callable[..., RecipeFeature]) -> None:
    recipes = {i: make_recipe(i, [i], title=f"두부조림 {i}번째") for i in (1, 2, 3)}
    ranked = [_scored(i, 0.9) for i in (1, 2, 3)]

    assert len(dish.collapse_versions(ranked, recipes, _names(), 2)) == 2
    assert dish.collapse_versions(ranked, recipes, _names(), 0) == ranked
    with pytest.raises(ValueError, match="max_per_dish"):
        RankingPolicy(max_per_dish=-1)


def test_a_candidate_without_features_is_kept(make_recipe: Callable[..., RecipeFeature]) -> None:
    """판본을 알 수 없는 것을 지우면 후보가 이유 없이 줄어듭니다."""
    recipes = {1: make_recipe(1, [1], title="두부조림")}

    kept = dish.collapse_versions([_scored(1, 0.9), _scored(99, 0.8)], recipes, _names(), 1)

    assert [item.recipe_id for item in kept] == [1, 99]


def test_the_served_list_has_one_of_each_dish_and_the_trace_counts_the_rest(
    make_recipe: Callable[..., RecipeFeature],
    make_candidate: Callable[..., Candidate],
    make_context: Callable[..., UserContext],
) -> None:
    titles = ["매콤 두부조림", "두부 조림 만들기", "감자조림", "알감자조림", "김치찌개", "콩나물국"]
    recipes = {
        i: make_recipe(i, [1], [i + 20], title=title) for i, title in enumerate(titles, start=1)
    }
    candidates = [make_candidate(i) for i in recipes]
    corpus = CorpusStats(flavor_mean=(0.5,) * 6, ingredient_names=_names())
    policy = replace(RankingPolicy(), exploration_ratio=0.0, cold_exploration_ratio=0.0)

    result = service.rank_candidates(candidates, recipes, make_context([1]), corpus, policy)

    served = [dish.dish_name(recipes[item.recipe_id].title, MAINS) for item in result.items]
    assert sorted(served) == ["감자조림", "김치찌개", "두부조림", "콩나물국"]
    rerank_stage = result.stages[1]
    assert rerank_stage.dropped["same_dish"] == 2
    assert rerank_stage.in_count == 6
    assert rerank_stage.out_count == 4
