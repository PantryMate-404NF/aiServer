"""문맥 모델 — 자기 재료의 유무와 `recipe_feature` 행의 변환."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from features.recommend.engine.candidate import FALLBACK_NONE, FALLBACK_POPULARITY, first_plan
from features.recommend.engine.context import (
    UserContext,
    build_context,
    recipe_feature_from_row,
)
from features.recommend.engine.persona import cold_persona
from features.recommend.policy import RankingPolicy

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "recommend" / "feature_golden.json"


def _golden_rows() -> list[dict[str, Any]]:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = data["recipes"]
    return rows


# ── 자기 재료 ────────────────────────────────────────────────────────────────


def test_without_the_split_the_whole_pantry_counts_as_the_users_own() -> None:
    """검사·평가 도구처럼 상비 재료 개념이 없는 호출자는 `own_pantry_ids` 를 안 넘깁니다."""
    assert UserContext(user_id=1, pantry_ids=frozenset({3})).has_own_ingredients
    assert not UserContext(user_id=1).has_own_ingredients


def test_a_staple_only_pantry_has_no_own_ingredients() -> None:
    """상비 재료(소금·간장)만 있는 신규 사용자. 재료 매칭으로 고를 것이 없습니다."""
    ctx = UserContext(user_id=1, pantry_ids=frozenset({1, 2, 3}), own_pantry_ids=frozenset())

    assert not ctx.has_own_ingredients
    assert ctx.pantry_ids == frozenset({1, 2, 3}), "조회와 f_pantry_use 는 여전히 전체를 본다"


def test_build_context_keeps_the_split() -> None:
    ctx = build_context(
        user_id=1,
        persona=cold_persona(),
        pantry_ids=[1, 2, 3, 40],
        own_pantry_ids=[40],
    )

    assert ctx.own_pantry_ids == frozenset({40})
    assert ctx.has_own_ingredients


def test_a_bare_pantry_skips_the_relax_ladder(policy: RankingPolicy) -> None:
    """09-18 실측: 빈 팬트리로 k 를 풀면 양념 제조법 94건이 나와 폴백이 안 걸립니다.

    그래서 첫 계획이 곧 인기순입니다. 자기 재료가 있으면 명세대로 k = 2 입니다.
    """
    bare = first_plan(policy, pantry_is_bare=True)
    assert bare.stage == FALLBACK_POPULARITY
    assert bare.max_missing == policy.max_missing_relaxed
    assert bare.degraded

    assert first_plan(policy, pantry_is_bare=False).stage == FALLBACK_NONE


# ── recipe_feature 행 → RecipeFeature ────────────────────────────────────────


def test_every_golden_row_converts_and_keeps_its_gaps() -> None:
    """골든 30건 전부 지나가고, 없는 값은 0 이 아니라 None 으로 남습니다."""
    rows = _golden_rows()
    features = [recipe_feature_from_row(row) for row in rows]

    assert [f.recipe_id for f in features] == [row["recipe_id"] for row in rows]
    for row, f in zip(rows, features, strict=True):
        assert len(f.flavor_vec) == 6
        assert f.essential_ids == frozenset(row["essential_ids"])
        assert f.all_ids == frozenset(row["all_ids"])
        assert (f.cook_minutes is None) == (row["cook_minutes"] is None)
        assert (f.difficulty is None) == (row["difficulty"] is None)
        assert f.popularity_score == pytest.approx(row["popularity_score"])
    assert any(f.cook_minutes is None for f in features), "골든에 조리시간 없는 행이 있다"
    assert any(f.difficulty is None for f in features), "골든에 난이도 없는 행이 있다"


@pytest.mark.parametrize(("stored", "expected"), [(1, 0.0), (2, 0.25), (3, 0.5), (5, 1.0)])
def test_difficulty_maps_one_to_five_onto_zero_to_one(stored: int, expected: float) -> None:
    f = recipe_feature_from_row({"recipe_id": 1, "difficulty": stored})
    assert f.difficulty == pytest.approx(expected)


@pytest.mark.parametrize(("word", "expected"), [("EASY", 0.0), ("NORMAL", 0.5), ("HARD", 1.0)])
def test_the_backend_three_step_difficulty_is_accepted(word: str, expected: float) -> None:
    """백엔드 `recipes.difficulty` 는 세 단계 열거형입니다 (09-21 실데이터).

    숫자로 바꾸려다 ValueError 로 터지던 자리입니다.
    """
    got = recipe_feature_from_row({"recipe_id": 1, "difficulty": word}).difficulty
    assert got == pytest.approx(expected)
    assert recipe_feature_from_row({"recipe_id": 1, "difficulty": "easy"}).difficulty == 0.0


@pytest.mark.parametrize("bad", ["", "VERY_HARD", "  ", 0, 9])
def test_an_unreadable_difficulty_is_unmeasured_not_easy(bad: object) -> None:
    """모르는 값을 0 으로 두면 '가장 쉬움' 으로 읽혀 f_skill_fit 이 틀린 값으로 돕니다."""
    assert recipe_feature_from_row({"recipe_id": 1, "difficulty": bad}).difficulty is None


def test_season_needs_the_month_to_become_a_score() -> None:
    vec = [0.1 * m for m in range(1, 13)]
    assert recipe_feature_from_row({"recipe_id": 1, "season_vec": vec}).season_score is None
    scored = recipe_feature_from_row({"recipe_id": 1, "season_vec": vec}, month=9)
    assert scored.season_score == pytest.approx(0.9)
    short = recipe_feature_from_row({"recipe_id": 1, "season_vec": [1.0]}, month=9)
    assert short.season_score is None, "12칸이 아니면 계산하지 않는다"


def test_cuisine_family_is_normalised_and_the_unknown_is_dropped() -> None:
    assert recipe_feature_from_row({"recipe_id": 1, "cuisine_family": "korean"}).cuisine == "korean"
    assert recipe_feature_from_row({"recipe_id": 1, "cuisine_family": "한식"}).cuisine == "korean"
    assert recipe_feature_from_row({"recipe_id": 1, "cuisine_family": "martian"}).cuisine is None
    assert recipe_feature_from_row({"recipe_id": 1}).cuisine is None


def test_a_row_with_only_an_id_is_a_feature_with_nothing_measured() -> None:
    f = recipe_feature_from_row({"recipe_id": 7})

    assert f.title == ""
    assert f.essential_ids == frozenset() and f.all_ids == frozenset()
    assert all(v is None for v in f.flavor_vec)
    assert f.popularity_score is None and f.quality_score is None
    assert f.cook_minutes is None and f.difficulty is None
    assert f.cuisine is None and f.dish_type is None and f.season_score is None
