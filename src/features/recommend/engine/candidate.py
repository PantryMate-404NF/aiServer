"""① Retrieval 의 완화 정책. 조회 자체는 `repository.retrieve` 가 합니다.

SQL 은 03 의 5절대로 repository 밖으로 나가지 않습니다. 여기서 정하는 것은 **한 번의
조회로 부족할 때 무엇을 다음으로 시도하는가** 뿐입니다.

`retrieve(max_missing=k)` 는 부족 재료가 k 이하인 후보만 돌려주므로, 그 결과 안에서
k 를 풀어 봐야 새 후보가 나오지 않습니다. 완화는 **다시 조회하는 것**이어야 합니다.
그래서 이 파일은 후보를 고르지 않고 다음 조회 인자를 돌려줍니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate

FALLBACK_NONE = "none"
FALLBACK_RELAX_MISSING = "relax_missing"
FALLBACK_POPULARITY = "popularity"


@dataclass(frozen=True)
class RetrievalPlan:
    """다음에 시도할 조회. `stage` 는 그 결과가 어느 단계의 산출인지 말합니다."""

    max_missing: int
    stage: str

    @property
    def degraded(self) -> bool:
        return self.stage != FALLBACK_NONE


def first_plan(policy: RankingPolicy) -> RetrievalPlan:
    """첫 조회. 명세의 k = 2 입니다."""
    return RetrievalPlan(max_missing=policy.max_missing, stage=FALLBACK_NONE)


def needed(policy: RankingPolicy, top_k: int) -> int:
    """후보가 이만큼은 있어야 완화를 멈춥니다.

    `min_candidates` 는 Top-20 을 전제한 값입니다. 더 달라는 요청에는 그만큼 필요하고,
    탐색 슬롯이 개인화 몫을 잠식하지 않도록 그 몫을 더합니다.
    """
    return max(policy.min_candidates, top_k + round(top_k * policy.exploration_ratio))


def next_plan(
    current: RetrievalPlan, found: int, policy: RankingPolicy, top_k: int
) -> RetrievalPlan | None:
    """이번 조회로 충분하면 None, 아니면 다음 조회 계획.

    부족수를 `max_missing_relaxed` 까지 한 단계씩 풀고, 그래도 모자라면 인기순입니다.
    인기순은 부족 재료를 보지 않으므로 `max_missing_relaxed` 를 그대로 싣습니다.
    """
    if found >= needed(policy, top_k):
        return None
    if current.max_missing < policy.max_missing_relaxed:
        return RetrievalPlan(current.max_missing + 1, FALLBACK_RELAX_MISSING)
    if current.stage != FALLBACK_POPULARITY:
        return RetrievalPlan(policy.max_missing_relaxed, FALLBACK_POPULARITY)
    return None


def dedupe(candidates: Sequence[Candidate]) -> list[Candidate]:
    """같은 레시피가 두 번 오면 먼저 온 것을 남깁니다. 완화 조회는 앞 결과를 포함합니다."""
    seen: set[int] = set()
    kept: list[Candidate] = []
    for candidate in candidates:
        if candidate.recipe_id in seen:
            continue
        seen.add(candidate.recipe_id)
        kept.append(candidate)
    return kept
