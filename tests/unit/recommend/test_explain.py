"""사유 문구. 조사 결합과 갈래별 문장의 완결성."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from features.recommend.engine.explain import (
    DEFAULT_REASON,
    BlockStats,
    attach_josa,
    block_stats,
    explain,
    salient_block,
)
from features.recommend.engine.rank import BLOCKS
from features.recommend.schema import CorpusStats, RecipeCandidate, ScoredCandidate, UserContext

NAMES = {4: "감자", 11: "계란", 14: "돼지고기"}
CORPUS = CorpusStats(flavor_mean=(0.5, 0.5, 0.5), ingredient_names=NAMES)
FLAT_STATS = {name: BlockStats(mean=0.5, std=0.1) for name in BLOCKS}


def _scored(
    recipe: RecipeCandidate, blocks: dict[str, float | None], missing: tuple[int, ...] = ()
) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=recipe, missing_ids=missing, blocks=blocks, base_score=0.5, score=0.5
    )


def _dominated_by(block: str) -> dict[str, float | None]:
    return {name: (1.0 if name == block else 0.5) for name in BLOCKS}


@pytest.mark.parametrize(
    ("word", "pair", "expected"),
    [
        ("감자", "이/가", "감자가"),
        ("계란", "이/가", "계란이"),
        ("우유", "은/는", "우유는"),
        ("김치", "을/를", "김치를"),
        ("닭고기", "과/와", "닭고기와"),
        ("귤", "으로/로", "귤로"),
        ("밥", "으로/로", "밥으로"),
        ("A", "이/가", "A가"),
        ("", "이/가", "가"),
    ],
)
def test_attach_josa(word: str, pair: str, expected: str) -> None:
    assert attach_josa(word, pair) == expected


@pytest.mark.parametrize("block", BLOCKS)
def test_each_block_yields_a_complete_sentence(
    block: str,
    make_recipe: Callable[..., RecipeCandidate],
    make_context: Callable[..., UserContext],
) -> None:
    """어느 블록이 뽑히든 자리표시자나 None 이 문장에 남지 않아야 합니다."""
    recipe = make_recipe(1, essential=[4, 11], flavor_vec=(0.9, 0.5, 0.5), cook_minutes=15)
    ctx = make_context(pantry=[4], expiring=[11], taste=(0.9, 0.5, 0.5), max_cook_minutes=30)
    item = _scored(recipe, _dominated_by(block), missing=(11,))

    reason = explain(item, ctx, CORPUS, FLAT_STATS, is_exploration=False)

    assert reason
    assert "None" not in reason
    assert "{" not in reason


def test_exploration_reason_mentions_the_cuisine(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    item = _scored(make_recipe(1, cuisine="양식"), _dominated_by("match"))

    reason = explain(item, make_context(), CORPUS, FLAT_STATS, is_exploration=True)

    assert "양식" in reason


def test_missing_ingredient_names_are_bound(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    item = _scored(make_recipe(1, essential=[4, 11]), _dominated_by("match"), missing=(4,))

    assert explain(item, make_context(), CORPUS, FLAT_STATS, is_exploration=False) == (
        "감자만 더 있으면 완성돼요"
    )


def test_nothing_missing_says_ready_to_cook(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    item = _scored(make_recipe(1, essential=[4]), _dominated_by("match"))

    reason = explain(item, make_context(), CORPUS, FLAT_STATS, is_exploration=False)

    assert reason == "지금 있는 재료만으로 바로 만들 수 있어요"


def test_expiring_names_get_the_right_josa(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    recipe = make_recipe(1, essential=[11, 14])
    ctx = make_context(pantry=[11, 14], expiring=[11, 14])
    item = _scored(recipe, _dominated_by("expiring"))

    reason = explain(item, ctx, CORPUS, FLAT_STATS, is_exploration=False)

    assert reason.startswith("계란, 돼지고기가 ")


def test_unknown_ingredient_names_fall_back_to_counts(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    nameless = CorpusStats(flavor_mean=(0.5, 0.5, 0.5))
    ctx = make_context(pantry=[7], expiring=[7, 8])
    missing = _scored(make_recipe(1, essential=[7, 8, 9]), _dominated_by("match"), missing=(8, 9))
    expiring = _scored(make_recipe(2, essential=[7, 8]), _dominated_by("expiring"))

    assert explain(missing, ctx, nameless, FLAT_STATS, is_exploration=False) == (
        "재료 2가지만 더 있으면 완성돼요"
    )
    assert explain(expiring, ctx, nameless, FLAT_STATS, is_exploration=False) == (
        "곧 소비기한이 끝나는 재료 2개를 쓸 수 있어요"
    )


def test_taste_reason_names_the_dominant_axis(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    recipe = make_recipe(1, flavor_vec=(0.5, 0.9, 0.5))
    ctx = make_context(taste=(0.5, 1.0, 0.5))
    item = _scored(recipe, _dominated_by("taste"))

    assert "단맛이" in explain(item, ctx, CORPUS, FLAT_STATS, is_exploration=False)


def test_context_reason_uses_cook_minutes(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    timed = _scored(make_recipe(1, cook_minutes=15), _dominated_by("ctx"))
    untimed = _scored(make_recipe(2, cook_minutes=None), _dominated_by("ctx"))

    assert (
        explain(timed, make_context(), CORPUS, FLAT_STATS, is_exploration=False)
        == "15분이면 완성돼요"
    )
    assert "None" not in explain(untimed, make_context(), CORPUS, FLAT_STATS, is_exploration=False)


def test_salient_block_prefers_the_highest_z_score(
    make_recipe: Callable[..., RecipeCandidate],
) -> None:
    """원점수는 quality 가 더 높아도 후보군 평균 대비로는 match 가 더 두드러집니다."""
    stats = {"match": BlockStats(0.2, 0.1), "quality": BlockStats(0.9, 0.1)}
    item = _scored(make_recipe(1), {"match": 0.6, "quality": 0.95, "taste": None})

    assert salient_block(item, stats) == "match"


def test_block_stats_skip_unmeasurable_values(make_recipe: Callable[..., RecipeCandidate]) -> None:
    scored = [
        _scored(make_recipe(1), {"match": 0.2, "expiring": None}),
        _scored(make_recipe(2), {"match": 0.6, "expiring": None}),
    ]

    stats = block_stats(scored)

    assert "expiring" not in stats
    assert stats["match"].mean == pytest.approx(0.4)
    assert stats["match"].std == pytest.approx(0.2)


def test_no_measurable_block_gives_the_default_reason(
    make_recipe: Callable[..., RecipeCandidate], make_context: Callable[..., UserContext]
) -> None:
    item = _scored(make_recipe(1), dict.fromkeys(BLOCKS))

    assert explain(item, make_context(), CORPUS, {}, is_exploration=False) == DEFAULT_REASON
