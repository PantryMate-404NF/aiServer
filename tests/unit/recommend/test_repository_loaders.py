"""② 가 읽는 피처·통계 로더 — DB 없이 SQL 의 모양과 변환 결과를 봅니다.

커서를 가짜로 바꿔 실행된 SQL 과 매개변수, 그리고 돌아온 행이 엔진 모델로 어떻게
바뀌는지만 봅니다. 실제 DB 와의 대조는 검증 기록의 수동 확인이 맡습니다.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from features.recommend import repository


class _FakeCursor:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._results = results

    def execute(self, sql: str, params: object = None) -> None:
        self.calls.append((" ".join(sql.split()), params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._results.pop(0)

    def fetchone(self) -> tuple[Any, ...] | None:
        rows = self._results.pop(0)
        return rows[0] if rows else None


@pytest.fixture
def fake_cursor(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _FakeCursor]:
    def install(results: list[list[tuple[Any, ...]]]) -> _FakeCursor:
        cur = _FakeCursor(results)

        @contextmanager
        def cursor(commit: bool = False) -> Iterator[_FakeCursor]:
            yield cur

        monkeypatch.setattr(repository, "cursor", cursor)
        return cur

    return install


def _feature_row(recipe_id: int, **over: object) -> tuple[Any, ...]:
    base: dict[str, Any] = {
        "recipe_id": recipe_id,
        "title": f"레시피 {recipe_id}",
        "essential_ids": [1, 2],
        "all_ids": [1, 2, 3],
        "flavor_vec": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        "popularity_score": 0.5,
        "quality_score": 0.0,
        "cook_minutes": 30,
        "difficulty": 3,
        "cuisine_family": "korean",
        "dish_type": "soup",
        "season_vec": [0.0] * 8 + [1.0] + [0.0] * 3,
    }
    base.update(over)
    return tuple(base[c] for c in repository._FEATURE_COLUMNS)


def test_features_are_read_by_id_and_test_rows_are_excluded_by_default(
    fake_cursor: Callable[..., _FakeCursor],
) -> None:
    cur = fake_cursor(
        [[_feature_row(10), _feature_row(11, cuisine_family=None, cook_minutes=None)]]
    )

    got = repository.load_recipe_features([10, 11, 12], month=9)

    sql, params = cur.calls[0]
    assert "FROM recipe_feature rf JOIN recipe r ON r.id = rf.recipe_id" in sql
    assert "rf.recipe_id = ANY(%s)" in sql and "NOT LIKE 'test-%%'" in sql
    assert params == ([10, 11, 12], False)
    assert set(got) == {10, 11}, "돌아오지 않은 12 는 피처 행이 없는 것이다"
    assert got[10].title == "레시피 10" and got[10].cuisine == "korean"
    assert got[10].difficulty == pytest.approx(0.5) and got[10].season_score == pytest.approx(1.0)
    assert got[11].cuisine is None and got[11].cook_minutes is None


def test_include_test_reaches_the_query_and_an_empty_ask_never_touches_the_db(
    fake_cursor: Callable[..., _FakeCursor],
) -> None:
    cur = fake_cursor([[]])
    assert repository.load_recipe_features([]) == {}
    assert cur.calls == []

    repository.load_recipe_features([1], include_test=True)
    assert cur.calls[0][1] == ([1], True)


def test_corpus_stats_carry_mu_idf_names_and_the_version(
    fake_cursor: Callable[..., _FakeCursor],
) -> None:
    mu = [0.087, 0.219, 0.184, 0.054, 0.282, 0.151]
    fake_cursor(
        [
            [(7, mu, 46353)],
            [(1, "소금", 20000), (2, "메밀면", 0), (3, "새우", 500)],
        ]
    )

    stats = repository.load_corpus_stats()

    assert stats.stats_version == 7
    assert stats.flavor_mean == pytest.approx(tuple(mu))
    assert stats.ingredient_idf[1] == pytest.approx(math.log(46353 / 20000))
    assert stats.ingredient_idf[3] == pytest.approx(math.log(46353 / 500))
    assert 2 not in stats.ingredient_idf, "한 번도 안 나온 재료는 기본값을 받게 뺀다"
    assert stats.ingredient_names == {1: "소금", 2: "메밀면", 3: "새우"}


def test_without_a_stats_row_the_mean_is_unknown_not_zero(
    fake_cursor: Callable[..., _FakeCursor],
) -> None:
    fake_cursor([[], [(1, "소금", 3)]])

    stats = repository.load_corpus_stats()

    assert stats.flavor_mean is None and stats.stats_version is None
    assert stats.ingredient_idf == {}
    assert stats.ingredient_names == {1: "소금"}
