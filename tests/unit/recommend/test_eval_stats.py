"""정책 비교와 판정. 순열 baseline, 유저 단위 부트스트랩, Interleaving, 검출력 (명세 6절)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from features.recommend.enums import EventType
from features.recommend.evaluation import stats, synth
from features.recommend.evaluation.labels import gains
from features.recommend.evaluation.record import EvalEvent, EvalRecord
from features.recommend.stage import RankedItem

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))


# ── 순열 baseline ─────────────────────────────────────────────────
def test_popularity_baseline_sorts_by_feature_with_none_last(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    def item(rank: int, popularity: float | None) -> RankedItem:
        features = make_item(rank).features | {"f_popularity": popularity}
        return make_item(rank, features=features)

    record = make_record(items=[item(1, 0.2), item(2, None), item(3, 0.9)])
    labels = {101: 0.1, 102: 0.2, 103: 0.3}

    assert stats.baseline_sequence(record, labels, "popularity", seed=0) == [0.3, 0.1, 0.2]


def test_random_baseline_is_reproducible_by_seed_and_differs_per_record(
    make_record: Callable[..., Any],
) -> None:
    labels = {rid: float(i) for i, rid in enumerate(range(101, 106))}
    record = make_record()
    first = stats.baseline_sequence(record, labels, "random", seed=3)
    assert first == stats.baseline_sequence(record, labels, "random", seed=3)
    assert sorted(first) == sorted(labels.values())
    # 기록마다 다른 순열. 시드 하나로 전부 같은 순열을 주면 "무작위" 가 아닙니다
    others = {
        tuple(stats.baseline_sequence(make_record(), labels, "random", seed=3)) for _ in range(20)
    }
    assert len(others) > 1


# ── 부트스트랩 ────────────────────────────────────────────────────
def test_comparing_an_ordering_with_itself_gives_a_ci_around_zero() -> None:
    _, records = synth.generate(synth.SynthSpec(users=25, requests=200, hit_rate=0.3, seed=4))
    served = stats.ndcg_by_user(records, k=10)

    diff = stats.paired_bootstrap(served, served, resamples=200, seed=0)

    assert diff is not None
    assert diff.ci95[0] <= 0.0 <= diff.ci95[1]
    assert diff.mean == pytest.approx(0.0)
    assert diff.se == 0.0


def test_bootstrap_is_reproducible_with_a_seed() -> None:
    values = {f"u{i}": [0.1 * i, 0.2 * i] for i in range(1, 30)}
    assert stats.bootstrap(values, resamples=100, seed=7) == stats.bootstrap(
        values, resamples=100, seed=7
    )


def test_bootstrap_resamples_users_not_requests() -> None:
    """한 유저의 요청을 통째로 가져오므로 유저가 하나면 CI 폭이 0 입니다."""
    one_user = {"u1": [0.0, 1.0, 0.0, 1.0]}
    estimate = stats.bootstrap(one_user, resamples=50, seed=0)
    assert estimate is not None
    assert estimate.ci95 == (0.5, 0.5)
    assert estimate.n_users == 1


def test_bootstrap_without_users_is_none() -> None:
    assert stats.bootstrap({}, resamples=10) is None
    assert stats.paired_bootstrap({"u1": [1.0]}, {"u2": [1.0]}, resamples=10) is None
    assert stats.verdict(None).status == "hold"


# ── Interleaving. 손 계산과 일치해야 합니다 ───────────────────────
def _interleaved(
    make_record: Callable[..., Any], make_item: Callable[..., Any], user: str, winner: str
) -> EvalRecord:
    items = [make_item(1, team="A"), make_item(2, team="B")]
    winning_recipe = 101 if winner == "A" else 102
    return make_record(
        user_hash=user,
        items=items,
        policies=[{"team": "A", "model_version": "a"}, {"team": "B", "model_version": "b"}],
        events=[
            EvalEvent(
                recipe_id=winning_recipe, event_type=EventType.CLICK, position=None, created_at=NOW
            )
        ],
    )


def test_interleaving_win_rate_and_p_value_match_hand_calculation(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    # 유저 3명: A, B, A 가 이김 → 승률 2/3. n=3, k=2 의 양측 이항 p 값은 1.0
    records = [
        _interleaved(make_record, make_item, "u1", "A"),
        _interleaved(make_record, make_item, "u2", "B"),
        _interleaved(make_record, make_item, "u3", "A"),
    ]
    result = stats.interleaving(records)
    assert result is not None
    assert result.pairs == 3
    assert result.win_rate == pytest.approx(2 / 3)
    assert result.p_value == pytest.approx(1.0)


def test_interleaving_five_users_all_a(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    # n=5, k=5 → p = 2 × 0.5^5 = 0.0625
    records = [_interleaved(make_record, make_item, f"u{i}", "A") for i in range(5)]
    result = stats.interleaving(records)
    assert result is not None
    assert result.win_rate == pytest.approx(1.0)
    assert result.p_value == pytest.approx(0.0625)


def test_interleaving_is_none_without_policies(make_record: Callable[..., Any]) -> None:
    assert stats.interleaving([make_record(), make_record()]) is None


# ── 검출력과 판정 ─────────────────────────────────────────────────
def test_power_formulas() -> None:
    # ((1.96 + 0.8416) × 0.1 / 0.02)^2 = 196.2 → 197명
    assert stats.users_needed(sd=0.1, effect=0.02) == 197
    assert stats.min_detectable(sd=0.1, n_users=100) == pytest.approx(0.028016, abs=1e-5)
    # 검출력은 판정과 같은 대응 부트스트랩의 산포에서 나옵니다: sd = se × √n
    diff = stats.Estimate(mean=0.0, ci95=(0.0, 0.0), n_users=100, se=0.01)
    block = stats.power(diff)
    assert block["min_detectable"] == pytest.approx(0.028016, abs=1e-5)
    assert block["users_needed_for"] == {"0.02": 197}
    assert stats.power(None)["min_detectable"] is None


def test_verdict_pass_hold_fail() -> None:
    up = stats.Estimate(mean=0.05, ci95=(0.01, 0.09), n_users=30, se=0.02)
    zero = stats.Estimate(mean=0.01, ci95=(-0.02, 0.04), n_users=30, se=0.015)
    down = stats.Estimate(mean=-0.05, ci95=(-0.09, -0.01), n_users=30, se=0.02)
    few = stats.Estimate(mean=0.05, ci95=(0.01, 0.09), n_users=stats.MIN_USERS - 1, se=0.02)

    assert stats.verdict(up).status == "pass"
    assert stats.verdict(zero).status == "hold"
    assert stats.verdict(down).status == "fail"
    assert stats.verdict(few).status == "hold"


def test_served_order_beats_coverage_baseline_when_clicks_decay_by_position() -> None:
    """심은 위치 감쇠가 있으면 서빙 순서가 재료 단독 순서보다 낫다고 판정합니다."""
    spec = synth.SynthSpec(users=50, requests=600, hit_rate=0.4, position_decay=0.5, seed=9)
    _, records = synth.generate(spec)
    served = stats.ndcg_by_user(records, k=10)
    baseline = stats.ndcg_by_user(records, k=10, baseline="coverage", seed=0)

    diff = stats.paired_bootstrap(served, baseline, resamples=300, seed=1)

    assert diff is not None
    assert stats.verdict(diff).status == "pass"


def test_ndcg_by_user_skips_records_without_positive(make_record: Callable[..., Any]) -> None:
    quiet = make_record(user_hash="u1")
    assert gains(quiet) == {101: 0.0, 102: 0.0, 103: 0.0, 104: 0.0, 105: 0.0}
    assert stats.ndcg_by_user([quiet], k=5) == {}
