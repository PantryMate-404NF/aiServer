"""흐름 조립과 로그 쓰기 계측 (04 3-1).

    from features.recommend.service import bump, counters, reset_counters

## 로그 실패가 추천을 실패시키지 않습니다

그렇다고 조용히 삼키면 데이터가 비어가는 것을 아무도 모릅니다. 그래서
**삼키되 반드시 셉니다.**

🔴 **다만 지금은 `counters()` 를 읽는 곳이 없습니다.** `/health` 도 대시보드도
   이 값을 싣지 않고 `QualityExtra.log_counters` 는 계약만 있고 채우는 코드가
   없습니다. 프로세스 메모리에만 쌓이므로, DB 를 붙인 뒤 로그 적재가 전부
   실패해도 API 는 200 을 돌려주고 아무도 모릅니다. DB 전환 점검표의 C-07 이
   이것이며, 그때까지 이 문단은 '셉니다' 까지만 사실입니다.

## `stage_trace.totals.degraded` 에 얹지 않습니다

두 가지 이유입니다.

  1. `degraded` 는 `stage_trace` 안에 있고 `stage_trace` 는 그 INSERT 로만
     저장됩니다. 쓰기가 실패하면 `degraded=true` 를 담은 행도 같이 사라집니다 -
     **실패를 실패한 것 안에 기록할 수는 없습니다.**
  2. `degraded` 의 정의는 "폴백 경로를 탔다" 이고 폴백율 대시보드의 분자입니다.
     로깅 결함을 섞으면 두 신호가 한 칸에서 합쳐져 다시 못 나눕니다.

그래서 프로세스 메모리에 세고 밖으로 노출합니다.

## ② Ranking 과 ③ Re-ranking 의 조립

`rank_candidates()` 가 두 단계를 잇습니다. DB 도 시각도 보지 않으므로 후보와 문맥만
주면 어디서든 돌고, 같은 입력이면 점수가 같습니다. ① Retrieval 은 `repository.retrieve`
가 하고, 후보가 모자랄 때 무엇을 다시 조회할지는 `engine/candidate.py` 가 정합니다.

⬜ ① 을 포함한 서빙 전체(요청 파싱, DB 조회, 로그 적재)는 `repository` 와 라우터가
   붙는 시점에 이 파일이 가져갑니다 - 02 의 3.2 가 흐름 조립을 여기로 정해 두었습니다.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter

from features.recommend.engine import rerank, score
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.enums import Stage
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate, RankedItem, ScoredCandidate, StageInfo

_LOCK = threading.Lock()
_C: dict[str, int] = {}

MILLISECONDS_PER_SECOND = 1000


def bump(key: str, n: int = 1) -> None:
    with _LOCK:
        _C[key] = _C.get(key, 0) + n


def counters() -> dict[str, int]:
    """현재 카운터 스냅샷. `/health` 와 대시보드가 읽습니다.

    프로세스 메모리입니다 - 재시작하면 0 이 됩니다. 유실을 **누적 총계**로
    보려면 대시보드가 주기적으로 긁어 시계열로 쌓아야 합니다.
    """
    with _LOCK:
        return dict(_C)


def reset_counters() -> None:
    """테스트 전용."""
    with _LOCK:
        _C.clear()


@dataclass(frozen=True)
class RankingResult:
    """②③ 의 산출과 그 과정. `stages` 는 그대로 `StageTrace.stages` 에 실립니다."""

    items: list[RankedItem]
    scored: list[ScoredCandidate]
    stages: list[StageInfo]

    @property
    def latency_ms(self) -> int:
        return sum(stage.latency_ms for stage in self.stages)


def rank_candidates(
    candidates: Sequence[Candidate],
    recipes: Mapping[int, RecipeFeature],
    ctx: UserContext,
    corpus: CorpusStats,
    policy: RankingPolicy,
    rng: random.Random,
    *,
    top_k: int = 20,
    weights: Mapping[str, float] | None = None,
    rng_seed: int = 0,
    max_missing_final: int | None = None,
    serving_mode: str = "real",
) -> RankingResult:
    """② 점수 계산 → ③ 재정렬. 제외는 하지 않습니다 - 그것은 ① 의 일입니다."""
    started = perf_counter()
    scored = score.score_all(
        candidates, recipes, ctx, corpus, policy, weights, max_missing=max_missing_final
    )
    ranking_ms = _elapsed(started)

    started = perf_counter()
    items = rerank.rerank(scored, recipes, ctx, corpus, policy, rng, top_k=top_k, weights=weights)
    rerank_ms = _elapsed(started)

    n_explore = sum(1 for item in items if item.is_exploration)
    params = policy.trace_params(
        top_k=top_k,
        n_explore=n_explore,
        rng_seed=rng_seed,
        max_missing_final=(policy.max_missing if max_missing_final is None else max_missing_final),
        serving_mode=serving_mode,
    )
    stages = [
        StageInfo(
            name=Stage.RANKING,
            in_count=len(candidates),
            out_count=len(scored),
            latency_ms=ranking_ms,
            strategy="linear-weighted",
            score_stats=_score_stats(scored),
            params=params,
        ),
        StageInfo(
            name=Stage.RERANK,
            in_count=len(scored),
            out_count=len(items),
            latency_ms=rerank_ms,
            strategy="mmr+mixed-exploration",
            dropped={"mmr_or_cap": max(0, len(scored) - len(items))},
            params=params,
            exploration_items=[item.recipe_id for item in items if item.is_exploration],
        ),
    ]
    return RankingResult(items=items, scored=scored, stages=stages)


def _score_stats(scored: Sequence[ScoredCandidate]) -> dict[str, float]:
    """점수 분포. 랭커가 조용히 납작해지는 것을 이 값으로 알아챕니다."""
    if not scored:
        return {}
    values = sorted(item.score for item in scored)
    return {
        "min": values[0],
        "p25": values[len(values) // 4],
        "p50": values[len(values) // 2],
        "p75": values[(len(values) * 3) // 4],
        "max": values[-1],
    }


def _elapsed(started: float) -> int:
    return int((perf_counter() - started) * MILLISECONDS_PER_SECOND)
