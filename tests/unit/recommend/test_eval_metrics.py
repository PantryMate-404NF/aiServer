"""순위·목록 지표. 골든 케이스, 외부 대조, 성질, 엔진과의 정합 (명세 5절, 10절)."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

import pytest

from features.recommend.engine.context import RecipeFeature
from features.recommend.engine.feature import jaccard_idf
from features.recommend.engine.rerank import mmr_select
from features.recommend.enums import FEATURE_KEYS
from features.recommend.evaluation.metrics import (
    catalog_coverage,
    exploration_positions,
    gain_sequence,
    ild,
    intra_list_distance,
    latency_percentiles,
    ndcg_at_k,
    position_ctr,
    recall_at_k,
    recall_user_level,
)
from features.recommend.stage import ScoredCandidate

LOG2_3 = 1.5849625007211563
LOG2_5 = 2.321928094887362


# ── 골든. 손으로 계산한 값입니다 ────────────────────────────────────
@pytest.mark.parametrize(
    ("gains", "k", "expected"),
    [
        ([1.0, 0.0, 0.0], 3, 1.0),  # 정답이 1위
        ([0.0, 1.0, 0.0], 3, 1.0 / LOG2_3),  # 정답이 2위: 1/log2(3)
        ([0.0, 0.0, 1.0], 2, 0.0),  # 정답이 K 밖. IDCG 는 1
        ([1.0, 0.0], 5, 1.0),  # K 가 목록보다 큼
        ([0.5, 0.5, 1.0], 3, (0.5 + 0.5 / LOG2_3 + 0.5) / (1.0 + 0.5 / LOG2_3 + 0.25)),  # 동점 gain
        ([0.0, 0.3, 0.0, 1.0], 4, (0.3 / LOG2_3 + 1.0 / LOG2_5) / (1.0 + 0.3 / LOG2_3)),  # 등급형
    ],
)
def test_ndcg_golden(gains: list[float], k: int, expected: float) -> None:
    assert ndcg_at_k(gains, k) == pytest.approx(expected)


def test_ndcg_without_positive_is_none() -> None:
    assert ndcg_at_k([0.0, 0.0, 0.0], 3) is None


@pytest.mark.parametrize(
    ("gains", "k", "expected"),
    [([0.0, 1.0, 0.0, 1.0], 2, 0.5), ([1.0, 1.0, 0.0], 1, 0.5), ([0.0, 0.0, 1.0], 2, 0.0)],
)
def test_recall_golden(gains: list[float], k: int, expected: float) -> None:
    assert recall_at_k(gains, k) == pytest.approx(expected)


def test_recall_without_positive_is_none() -> None:
    assert recall_at_k([0.0, 0.0], 2) is None


def test_recall_user_level_counts_cooked_in_top_k() -> None:
    assert recall_user_level([1, 2, 3, 4], {2, 4, 9}, k=2) == pytest.approx(1 / 3)
    assert recall_user_level([1, 2], set(), k=2) is None


def test_gain_sequence_reranks_without_exploration(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    record = make_record(
        items=[
            make_item(1, is_exploration=True, propensity=0.2, explore_source="uniform"),
            make_item(2),
            make_item(3),
        ]
    )
    gains = {101: 1.0, 102: 0.0, 103: 1.0}

    assert gain_sequence(record, gains) == [1.0, 0.0, 1.0]
    assert gain_sequence(record, gains, include_exploration=False) == [0.0, 1.0]
    assert ndcg_at_k(gain_sequence(record, gains, include_exploration=False), 2) == pytest.approx(
        1.0 / LOG2_3
    )


# ── 외부 대조. 우리 nDCG 가 맞는지 확인할 유일한 바깥 잣대입니다 ────────
def test_ndcg_matches_sklearn_on_random_cases() -> None:
    sklearn_metrics = pytest.importorskip("sklearn.metrics")
    rng = random.Random(7)  # noqa: S311  # 검사용 난수
    for _ in range(100):
        n = rng.randint(2, 20)
        k = rng.randint(1, n)
        gains = [rng.choice([0.0, 0.0, 0.3, 0.6, 1.0]) for _ in range(n)]
        if not any(gains):
            continue
        # sklearn 은 예측 점수 순으로 정렬하므로 순위를 그대로 점수로 줍니다
        scores = [float(n - i) for i in range(n)]
        expected = sklearn_metrics.ndcg_score([gains], [scores], k=k)
        assert ndcg_at_k(gains, k) == pytest.approx(expected, abs=1e-9)


# ── 성질 ─────────────────────────────────────────────────────────
def test_moving_a_positive_up_never_lowers_ndcg() -> None:
    rng = random.Random(3)  # noqa: S311  # 검사용 난수
    for _ in range(50):
        gains = [rng.choice([0.0, 0.0, 0.5, 1.0]) for _ in range(10)]
        # 바로 위 항목보다 gain 이 큰 것을 한 칸 올립니다. 위가 더 크면 내려가는 것이 맞습니다
        movable = [i for i in range(1, len(gains)) if gains[i] > gains[i - 1]]
        if not movable:
            continue
        i = rng.choice(movable)
        moved = gains[:]
        moved[i - 1], moved[i] = moved[i], moved[i - 1]
        before, after = ndcg_at_k(gains, 10), ndcg_at_k(moved, 10)
        assert before is not None and after is not None and after >= before - 1e-12


def test_permuting_items_beyond_k_is_invariant() -> None:
    """K 밖 항목의 순서는 DCG@K 에도 IDCG 에도 영향이 없습니다."""
    gains = [0.0, 1.0, 0.0, 0.5, 0.3, 0.0]
    shuffled = [*gains[:3], 0.0, 0.3, 0.5]
    assert ndcg_at_k(gains, 3) == pytest.approx(ndcg_at_k(shuffled, 3))
    assert recall_at_k(gains, 3) == recall_at_k(shuffled, 3)


def test_shuffling_labels_changes_the_metric() -> None:
    assert ndcg_at_k([1.0, 0.0, 0.0], 3) != ndcg_at_k([0.0, 0.0, 1.0], 3)


# ── 목록 지표와 엔진 정합 ─────────────────────────────────────────
def test_ild_uses_the_same_similarity_as_mmr() -> None:
    """`metrics.ild` 의 유사도가 `rerank.mmr_select` 의 인라인 계산과 같습니다."""
    idf = {1: 2.0, 2: 0.5, 3: 1.5}
    a, b = frozenset({1, 2}), frozenset({2, 3, 4})
    recipes = {
        1: RecipeFeature(recipe_id=1, all_ids=a),
        2: RecipeFeature(recipe_id=2, all_ids=b),
    }
    empty = dict.fromkeys(FEATURE_KEYS)
    pool = [
        ScoredCandidate(recipe_id=1, missing_count=0, coverage=1.0, features=empty, score=0.9),
        ScoredCandidate(recipe_id=2, missing_count=0, coverage=1.0, features=empty, score=0.8),
    ]
    _, penalty_of_second = mmr_select(pool, recipes, count=2, idf=idf, lambda_=0.7)[1]

    assert 1.0 - ild([a, b], idf) == pytest.approx(penalty_of_second, abs=1e-6)
    assert 1.0 - ild([a, b], idf) == pytest.approx(jaccard_idf(a, b, idf))


def test_plain_ild_matches_the_mock_script_definition() -> None:
    a, b, c = frozenset({1, 2}), frozenset({2, 3}), frozenset({9})
    assert intra_list_distance([a, b, c]) == pytest.approx((2 / 3 + 1.0 + 1.0) / 3)
    assert intra_list_distance([a]) == 0.0


def test_catalog_coverage_counts_unique_recipes() -> None:
    assert catalog_coverage([1, 1, 2, 3], catalog_size=10) == pytest.approx(0.3)


def test_position_ctr_is_per_position_share_of_positive_gain() -> None:
    sequences = [[1.0, 0.0, 0.0], [0.0, 0.0], [1.0, 0.5, 0.0]]
    assert position_ctr(sequences) == pytest.approx([2 / 3, 1 / 3, 0.0])


def test_latency_percentiles_nearest_rank() -> None:
    assert latency_percentiles(list(range(1, 101))) == {"p50": 50.0, "p95": 95.0}
    assert latency_percentiles([]) == {"p50": None, "p95": None}


def test_exploration_positions_histogram(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    records = [
        make_record(items=[make_item(1), make_item(2, is_exploration=True, propensity=0.1)]),
        make_record(items=[make_item(1), make_item(2, is_exploration=True, propensity=0.1)]),
    ]
    assert exploration_positions(records) == {2: 2}
