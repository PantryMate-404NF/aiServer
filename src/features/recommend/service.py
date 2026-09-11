"""흐름 조립과 로그 쓰기 계측 (04 3-1).

    from features.recommend.service import bump, counters, reset_counters

## 로그 실패가 추천을 실패시키지 않습니다

그렇다고 조용히 삼키면 데이터가 비어가는 것을 아무도 모릅니다. 그래서
**삼키되 반드시 셉니다.**

🔴 **다만 지금은 `counters()` 를 읽는 곳이 없습니다.** `/health` 도 대시보드도
   이 값을 싣지 않고 `QualityExtra.log_counters` 는 계약만 있고 채우는 코드가
   없습니다. 프로세스 메모리에만 쌓이므로, DB 를 붙인 뒤 로그 적재가 전부
   실패해도 API 는 200 을 돌려주고 아무도 모릅니다. DB 전환 점검표의 M-07 이
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

## 취향 페르소나의 조립

`PersonaService` 가 온보딩 저장, 이벤트 기록, 페르소나 조회를 잇습니다. 수학은
`engine/persona.py` 에, 저장은 `profile_store.py` 에 있고 여기는 둘을 붙이는 일만 합니다.
3축 척도의 범위 변환(계약 0~4 → 0~1)은 `onboarding_profile()` 한 곳에서만 합니다.
"""

from __future__ import annotations

import math
import random
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from time import perf_counter
from uuid import UUID

from features.recommend.engine import persona as persona_engine
from features.recommend.engine import rerank, score
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.engine.persona import Persona, TasteEvent, TasteProfile
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import EventType, Stage
from features.recommend.policy import RankingPolicy, with_trace_extra
from features.recommend.profile_store import ProfileStore
from features.recommend.schema import EventIn
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


#: 3축 척도 계약의 상한. `OnboardingIn.scales` 는 0~4 이고 엔진은 0~1 만 받습니다.
SCALE_MAX = 4.0
#: 확정 온보딩 문항의 최소 선택 수. 계약이 개수를 프론트의 일로 두므로 서버는 거부하지 않고 셉니다.
MIN_PICKS = 3
#: 사용자 파일의 읽고-합치고-쓰기를 프로세스 안에서 직렬화합니다. 프로세스가 여럿이면 M-14 입니다.
_PROFILE_LOCK = threading.Lock()


def onboarding_profile(
    user_id: int,
    picks: Sequence[int],
    scales: Sequence[int] | None,
    presented: Sequence[FlavorVector],
    now: datetime,
) -> TasteProfile:
    """온보딩 응답을 취향 원본으로 바꿉니다. 범위 밖 인덱스와 척도는 거부합니다.

    조용히 받거나 잘라 넣으면 다른 음식의 맛이 취향이 되고 에러는 나지 않습니다. 같은 음식을
    두 번 고른 것은 한 번으로 둡니다 - 평균이 그쪽으로 두 배 기울 이유가 없습니다. 척도는
    여기서 0~1 로 옮기며, 이 변환은 저장소에도 엔진에도 없습니다.
    """
    unique = tuple(dict.fromkeys(int(i) for i in picks))
    if len(unique) != len(picks):
        bump("persona_pick_duplicate", len(picks) - len(unique))
    bad = [i for i in unique if not 0 <= i < len(presented)]
    if bad:
        bump("persona_pick_out_of_range")
        raise ValueError(f"제시 목록 밖의 인덱스입니다: {bad}")
    if 0 < len(unique) < MIN_PICKS:
        bump("persona_picks_under_min")
    return TasteProfile(
        user_id=user_id,
        picks=unique,
        pick_flavors=tuple(presented[i] for i in unique),
        scales=None if scales is None else _normalized_scales(scales),
        updated_at=now,
    )


def _normalized_scales(scales: Sequence[int]) -> tuple[float, ...]:
    """계약 범위(0~SCALE_MAX)를 확인하고 0~1 로 옮깁니다. 잘라 넣지 않습니다."""
    bad = [s for s in scales if not (math.isfinite(s) and 0 <= s <= SCALE_MAX)]
    if bad:
        bump("persona_scale_out_of_range")
        raise ValueError(f"척도는 0~{SCALE_MAX:g} 이어야 합니다: {bad}")
    return tuple(s / SCALE_MAX for s in scales)


@dataclass(frozen=True)
class PersonaService:
    """온보딩·이벤트·조회를 하나의 저장소 위에서 잇습니다.

    시계를 갖지 않습니다. `now` 는 호출자가 넘기며, 검사에서는 고정 시각을 씁니다.
    """

    store: ProfileStore
    #: 온보딩 제시 목록의 6축. `profile_store.load_presented_flavors()` 가 만듭니다.
    presented: Sequence[FlavorVector]
    policy: RankingPolicy

    def save_onboarding(
        self, user_id: int, picks: Sequence[int], scales: Sequence[int] | None, now: datetime
    ) -> Persona:
        """고른 음식과 척도 원본을 저장하고 페르소나를 돌려줍니다.

        이미 있는 이벤트는 지키므로 온보딩을 다시 해도 이력이 사라지지 않습니다. 저장 전에
        이벤트를 정책 상한으로 잘라냅니다.
        """
        profile = onboarding_profile(user_id, picks, scales, self.presented, now)
        with _PROFILE_LOCK:
            before = self.store.load(user_id)
            if before is not None:
                kept = persona_engine.prune_events(before.events, now, self.policy)
                profile = replace(profile, events=kept)
            self.store.save(profile)
        bump("persona_onboarded")
        return persona_engine.derive_persona(profile, now, self.policy)

    def record_events(
        self,
        events: Sequence[EventIn],
        flavor_of: Callable[[int], FlavorVector | None],
        now: datetime,
    ) -> int:
        """긍정 신호를 취향 이벤트로 저장합니다. 저장한 수를 돌려줍니다.

        무시·저장 취소처럼 무게가 0 인 종류, 레시피가 없는 이벤트, 범위 밖 별점, 맛을 모르는
        레시피, 한 배치 안의 같은 이벤트는 저장하지 않고 **셉니다.** 삼키기만 하면 취향이 안
        쌓이는 것을 아무도 모릅니다.

        시각은 서버 수신 시각 `now` 입니다 - `EventIn` 에 시각이 없습니다. 사용자마다
        읽고-합치고-쓰기를 하며 프로세스 안에서는 잠금으로 직렬화합니다. 프로세스가 여럿이면
        나중 쓰기가 앞 쓰기를 덮습니다 - 실 DB 로 옮기면(M-14) 사라지는 제약입니다.
        """
        persona_engine.require_aware(now, "now")
        added: dict[int, list[TasteEvent]] = {}
        seen: set[tuple[int, int, EventType, UUID | None]] = set()
        for event in events:
            if event.recipe_id is None:
                continue
            key = (event.user_id, event.recipe_id, event.event_type, event.request_id)
            if key in seen:
                bump("persona_event_duplicate")
                continue
            seen.add(key)
            made = _taste_event(event, event.recipe_id, flavor_of, now)
            if made is not None:
                added.setdefault(event.user_id, []).append(made)
        stored = 0
        with _PROFILE_LOCK:
            for user_id, fresh in added.items():
                before = self.store.load(user_id) or TasteProfile(user_id=user_id)
                kept = persona_engine.prune_events(before.events + tuple(fresh), now, self.policy)
                self.store.save(replace(before, events=kept, updated_at=now))
                stored += len(fresh)
        bump("persona_events_stored", stored)
        return stored

    def persona_for(self, user_id: int, now: datetime) -> Persona:
        """저장된 원본에서 페르소나를 만듭니다. 원본이 없으면 취향 없는 사용자입니다.

        원본 파일이 깨져 있으면 추천을 실패시키지 않고 취향 없는 사용자로 다루되
        **셉니다.** 저장소는 예외를 올리고(조용한 빈 취향 금지), 서빙은 여기서 받습니다.
        """
        try:
            with _PROFILE_LOCK:
                profile = self.store.load(user_id)
        except ValueError:
            bump("persona_profile_unreadable")
            return persona_engine.cold_persona()
        if profile is None:
            bump("persona_missing")
            return persona_engine.cold_persona()
        return persona_engine.derive_persona(profile, now, self.policy)


def _taste_event(
    event: EventIn, recipe_id: int, flavor_of: Callable[[int], FlavorVector | None], now: datetime
) -> TasteEvent | None:
    """저장할 이벤트면 만들고, 아니면 이유를 세고 None 입니다.

    낮은 별점은 음의 신호라 '무시' 이고, 범위 밖이거나 값이 없는 별점은 '잘못된 이벤트' 입니다.
    맛을 하나도 모르는 레시피(`flavor_vec` 이 전부 None)는 모르는 레시피와 같습니다.
    """
    if event.event_type is EventType.RATING and (
        event.value is None
        or not persona_engine.RATING_MIN <= event.value <= persona_engine.RATING_MAX
    ):
        bump("persona_event_invalid")
        return None
    if persona_engine.kind_weight(event.event_type, event.value) <= 0.0:
        bump("persona_event_ignored")
        return None
    flavor = flavor_of(recipe_id)
    if flavor is None or all(v is None for v in flavor):
        bump("persona_recipe_unknown")
        return None
    return TasteEvent(
        recipe_id=recipe_id, kind=event.event_type, at=now, flavor=flavor, value=event.value
    )


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
    # 후보가 모자라 탐색이 줄어든 만큼입니다. 0 이 아니면 ① 이 덜 가져온 것입니다.
    shortfall = max(0, round(len(items) * rerank.exploration_ratio(ctx, policy)) - n_explore)
    if shortfall:
        bump("explore_shortfall", shortfall)
    params = policy.trace_params(
        top_k=top_k,
        n_explore=n_explore,
        rng_seed=rng_seed,
        max_missing_final=(policy.max_missing if max_missing_final is None else max_missing_final),
        serving_mode=serving_mode,
    )
    params = with_trace_extra(params, {**_persona_params(ctx), **_explore_params(scored, policy)})
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
            dropped={
                "mmr_or_cap": max(0, len(scored) - len(items)),
                "explore_shortfall": shortfall,
            },
            params=params,
            exploration_items=[item.recipe_id for item in items if item.is_exploration],
        ),
    ]
    return RankingResult(items=items, scored=scored, stages=stages)


def _persona_params(ctx: UserContext) -> dict[str, object]:
    """평가가 취향 유무로 갈라 볼 수 있게 출처와 상태를 추적에 싣습니다."""
    if ctx.persona is None:
        return {}
    return {
        "persona_source": ctx.persona.prior_source.value,
        "persona_mode": ctx.persona.mode.value,
        "persona_events": ctx.persona.n_events,
        "persona_behavior_weight": round(ctx.persona.behavior_weight, 3),
    }


def _explore_params(scored: Sequence[ScoredCandidate], policy: RankingPolicy) -> dict[str, object]:
    """군집 없이 균등으로 폴백했으면 로그에 남기고 셉니다.

    `uniform_share` 는 동결 키라 덮지 않습니다. 평가는 `explore_fallback` 이 있으면 그 요청의
    실효 균등 비율을 1.0 으로 읽습니다.
    """
    if rerank.effective_uniform_share(scored, policy) == policy.uniform_share:
        return {}
    bump("explore_uniform_fallback")
    return {"explore_fallback": "uniform"}


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
