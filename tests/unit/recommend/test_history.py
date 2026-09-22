"""취향 이벤트 + 메모리 사전 → `UserHistory` (`engine/history.py`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from features.recommend.engine import history
from features.recommend.engine.context import RecipeFeature
from features.recommend.engine.persona import TasteEvent
from features.recommend.enums import EventType
from features.recommend.policy import RankingPolicy

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
POLICY = RankingPolicy()
FLAVOR = (0.5,) * 6
SALT = 99
RECIPES = {
    1: RecipeFeature(recipe_id=1, title="두부조림", all_ids=frozenset({1, 2, SALT})),
    2: RecipeFeature(recipe_id=2, title="새우볶음", all_ids=frozenset({3, 4, SALT})),
    3: RecipeFeature(recipe_id=3, title="감자전", all_ids=frozenset({5, SALT})),
}
STAPLES = frozenset({SALT})


def event(
    recipe_id: int, kind: EventType, days_ago: float = 0.0, value: float | None = None
) -> TasteEvent:
    return TasteEvent(
        recipe_id=recipe_id,
        kind=kind,
        at=NOW - timedelta(days=days_ago),
        flavor=FLAVOR,
        value=value,
    )


def build(*events: TasteEvent, recent: tuple[int, ...] = ()) -> history.UserHistory:
    return history.build_history(events, RECIPES, STAPLES, NOW, POLICY, recent_served=recent)


def test_no_events_means_no_history_not_an_empty_preference() -> None:
    """None 과 0 은 다릅니다. 이력이 없으면 두 신호는 측정 불가여야 합니다(`_preference`)."""
    assert build() == history.UserHistory()


def test_recent_cooks_bring_their_ingredients_and_titles_in_order() -> None:
    past = build(event(1, EventType.COOK, days_ago=5), event(2, EventType.COOK, days_ago=1))

    assert past.cooked_recipe_ids == frozenset({1, 2})
    assert past.cooked_titles == ("새우볶음", "두부조림")
    assert past.cooked_ingredient_sets == (frozenset({3, 4, SALT}), frozenset({1, 2, SALT}))


def test_cooks_older_than_the_window_and_unknown_recipes_are_not_history() -> None:
    past = build(
        event(1, EventType.COOK, days_ago=history.COOKED_WINDOW_DAYS + 1),
        event(404, EventType.COOK, days_ago=1),
        event(2, EventType.SAVE, days_ago=1),
    )

    assert past.cooked_recipe_ids == frozenset()
    assert past.cooked_titles == ()


def test_the_same_recipe_cooked_twice_is_one_entry_and_the_cap_holds() -> None:
    many = {
        i: RecipeFeature(recipe_id=i, title=f"요리{i}", all_ids=frozenset({i}))
        for i in range(1, 40)
    }
    events = [event(i, EventType.COOK, days_ago=i / 10) for i in range(1, 40)]
    events.append(event(1, EventType.COOK, days_ago=0))

    past = history.build_history(events, many, frozenset(), NOW, POLICY)

    assert len(past.cooked_recipe_ids) == history.MAX_COOKED
    assert past.cooked_titles[0] == "요리1"


def test_one_cook_makes_its_ingredients_liked_but_the_staple_is_not() -> None:
    past = build(event(1, EventType.COOK))

    assert past.liked_ingredient_ids == frozenset({1, 2})


def test_a_single_click_is_too_weak_but_two_saves_are_enough() -> None:
    assert build(event(2, EventType.CLICK)).liked_ingredient_ids == frozenset()
    assert build(
        event(2, EventType.SAVE), event(2, EventType.SAVE)
    ).liked_ingredient_ids == frozenset({3, 4})


def test_old_events_fade_with_the_same_half_life_as_the_taste_vector() -> None:
    fresh = build(event(3, EventType.COOK, days_ago=0))
    faded = build(event(3, EventType.COOK, days_ago=POLICY.persona_half_life_days * 2))

    assert fresh.liked_ingredient_ids == frozenset({5})
    assert faded.liked_ingredient_ids == frozenset()  # 1.0 x 0.25 < 하한


def test_a_low_rating_and_a_dismiss_do_not_make_ingredients_liked() -> None:
    past = build(event(1, EventType.RATING, value=1.0), event(1, EventType.DISMISS))

    assert past.liked_ingredient_ids == frozenset()


def test_liked_ingredients_are_capped_by_weight_then_id() -> None:
    wide = {1: RecipeFeature(recipe_id=1, title="잡탕", all_ids=frozenset(range(100, 140)))}
    heavy = {2: RecipeFeature(recipe_id=2, title="집중", all_ids=frozenset({100, 101}))}
    past = history.build_history(
        [event(1, EventType.COOK), event(2, EventType.COOK)],
        {**wide, **heavy},
        frozenset(),
        NOW,
        POLICY,
    )

    assert len(past.liked_ingredient_ids) == history.MAX_LIKED
    assert {100, 101} <= past.liked_ingredient_ids
    assert 139 not in past.liked_ingredient_ids


def test_recently_served_ids_are_passed_through_for_the_repeat_penalty() -> None:
    assert build(recent=(7, 8)).recent_recipe_ids == frozenset({7, 8})
