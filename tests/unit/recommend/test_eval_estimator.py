"""오프폴리시 추정. SNIPS, 지원 진단, 가중치 교체 목표 정책 (명세 7절)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from features.recommend.enums import EventType
from features.recommend.evaluation import estimator, synth
from features.recommend.evaluation.record import EvalEvent, EvalRecord
from features.recommend.stage import RankedItem

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))


def _click(recipe_id: int) -> EvalEvent:
    return EvalEvent(recipe_id=recipe_id, event_type=EventType.CLICK, position=None, created_at=NOW)


def test_logged_policy_as_target_reproduces_the_observed_mean(
    make_record: Callable[..., Any],
) -> None:
    """확률이 전부 1 이면 SNIPS 는 노출 항목 gain 의 단순 평균입니다."""
    records = [
        make_record(user_hash=f"u{i}", events=[_click(101)]) for i in range(3)
    ]  # 5개 중 1개 클릭 → gain 0.3 / 5

    result = estimator.estimate(records, estimator.logged_policy, name="logged")

    assert result.snips == pytest.approx(0.3 / 5)
    assert result.unsupported_ratio == 0.0
    assert result.propensity_below_one_ratio == 0.0
    assert result.usable is True


def test_unsupported_target_items_make_the_estimate_unusable(
    make_record: Callable[..., Any],
) -> None:
    records = [make_record(user_hash=f"u{i}", events=[_click(101)]) for i in range(3)]

    def target(record: EvalRecord) -> list[int]:
        return [999, *[item.recipe_id for item in record.items][:4]]

    result = estimator.estimate(records, target, name="bad")

    assert result.unsupported_ratio == pytest.approx(1 / 5)
    assert result.usable is False
    assert any("미지원" in reason for reason in result.reasons)


def test_weights_and_ess_follow_the_propensities(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    """p = 1 과 p = 0.5 → w = 1, 2. ESS = (1+2)² / (1+4) = 1.8. SNIPS = (1·g₁ + 2·g₂) / 3."""
    items = [
        make_item(1),
        make_item(2, is_exploration=True, propensity=0.5, explore_source="uniform"),
    ]
    record = make_record(items=items, events=[_click(102)])

    result = estimator.estimate([record], estimator.logged_policy, name="logged")

    assert result.ess == pytest.approx(1.8)
    assert result.snips == pytest.approx((1 * 0.0 + 2 * 0.3) / 3)
    assert result.propensity_below_one_ratio == pytest.approx(0.5)
    assert result.by_source["uniform"].ess == pytest.approx(1.0)
    assert result.by_source["uniform"].snips == pytest.approx(0.3)


def test_low_ess_is_reported_as_unusable_with_exposures_needed(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    """가중치 하나가 1000 이고 나머지가 1 이면 ESS ≈ 1.2 라 유저 100명의 10% 에 못 미칩니다."""
    records = [make_record(user_hash=f"u{i}") for i in range(99)]
    records.append(
        make_record(
            user_hash="u99",
            items=[make_item(1, is_exploration=True, propensity=0.001, explore_source="uniform")],
        )
    )

    def target(record: EvalRecord) -> list[int]:
        return [record.items[0].recipe_id]

    result = estimator.estimate(records, target, name="t")

    assert result.usable is False
    assert result.exposures_needed > 0


def test_weight_swap_policy_reranks_candidates_by_new_weights(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    def item(rank: int, popularity: float) -> RankedItem:
        base = make_item(rank)
        return make_item(rank, features=base.features | {"f_popularity": popularity})

    items = [item(1, 0.1), item(2, 0.9), item(3, 0.5)]
    record = make_record(items=items, candidates=items)
    policy = estimator.weight_swap_policy({"f_popularity": 1.0}, idf={}, lambda_=1.0)

    assert policy(record) == [102, 103, 101]


def test_weight_swap_policy_multiplies_the_logged_penalty(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    high = make_item(1, features=make_item(1).features | {"f_popularity": 0.9}, penalty=0.1)
    low = make_item(2, features=make_item(2).features | {"f_popularity": 0.5})
    record = make_record(items=[high, low], candidates=[high, low])
    policy = estimator.weight_swap_policy({"f_popularity": 1.0}, idf={}, lambda_=1.0)

    assert policy(record) == [102, 101]


def test_no_records_is_not_usable() -> None:
    result = estimator.estimate([], estimator.logged_policy, name="empty")
    assert result.usable is False
    assert result.snips is None


def test_synth_logged_policy_is_fully_supported() -> None:
    _, records = synth.generate(synth.SynthSpec(users=20, requests=100, hit_rate=0.3, seed=3))
    result = estimator.estimate(records, estimator.logged_policy, name="logged")
    assert result.unsupported_ratio == 0.0
    assert set(result.by_source) == {"uniform", "thompson"}
