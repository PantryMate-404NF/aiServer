"""① Retrieval 완화 정책. 조회는 repository 가 하고 여기서는 다음 인자만 정합니다."""

from __future__ import annotations

from collections.abc import Callable

from features.recommend.engine.candidate import (
    FALLBACK_NONE,
    FALLBACK_POPULARITY,
    FALLBACK_RELAX_MISSING,
    dedupe,
    first_plan,
    needed,
    next_plan,
)
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate


def test_first_plan_uses_the_spec_default(policy: RankingPolicy) -> None:
    plan = first_plan(policy)

    assert plan.max_missing == 2
    assert plan.stage == FALLBACK_NONE
    assert not plan.degraded


def test_enough_candidates_stop_the_relaxation(policy: RankingPolicy) -> None:
    plan = first_plan(policy)

    assert next_plan(plan, found=needed(policy, 20), policy=policy, top_k=20) is None


def test_relaxing_missing_count_comes_first(policy: RankingPolicy) -> None:
    plan = first_plan(policy)

    relaxed = next_plan(plan, found=0, policy=policy, top_k=20)

    assert relaxed is not None
    assert relaxed.max_missing == 3
    assert relaxed.stage == FALLBACK_RELAX_MISSING
    assert relaxed.degraded


def test_popularity_is_the_last_resort(policy: RankingPolicy) -> None:
    plan = first_plan(policy)
    for _ in range(policy.max_missing_relaxed - policy.max_missing):
        stepped = next_plan(plan, found=0, policy=policy, top_k=20)
        assert stepped is not None
        plan = stepped

    assert plan.max_missing == policy.max_missing_relaxed

    last = next_plan(plan, found=0, policy=policy, top_k=20)
    assert last is not None
    assert last.stage == FALLBACK_POPULARITY
    assert next_plan(last, found=0, policy=policy, top_k=20) is None


def test_needed_leaves_room_for_the_exploration_slots(policy: RankingPolicy) -> None:
    """탐색 4칸은 잔여 후보 상위 절반에 슬롯의 2배수가 있어야 채워집니다.

    슬롯 수만 더한 24건에서는 4칸 중 1칸만 채워졌습니다(F-04). 취향을 모르는 사용자는 8칸이라
    그만큼 더 필요합니다.
    """
    assert needed(policy, 20) == 36
    assert needed(policy, 20, exploration_ratio=policy.cold_exploration_ratio) == 52
    assert needed(policy, 10) >= policy.min_candidates


def test_dedupe_keeps_the_first_occurrence(make_candidate: Callable[..., Candidate]) -> None:
    """완화 조회는 앞 결과를 다시 포함합니다. 나중 것으로 덮으면 순서가 흔들립니다."""
    rows = [
        make_candidate(1, missing_count=0),
        make_candidate(2),
        make_candidate(1, missing_count=3),
    ]

    kept = dedupe(rows)

    assert [row.recipe_id for row in kept] == [1, 2]
    assert kept[0].missing_count == 0
