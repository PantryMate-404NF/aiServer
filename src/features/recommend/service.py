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
from dataclasses import dataclass, field, replace
from datetime import datetime
from time import perf_counter
from uuid import UUID

from features.recommend.engine import dish, rerank, score
from features.recommend.engine import persona as persona_engine
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.engine.persona import Persona, TasteEvent, TasteProfile
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import (
    FEATURE_KEYS,
    ONBOARDING_CUISINES,
    EventType,
    Stage,
    normalize_cuisine,
)
from features.recommend.policy import RankingPolicy, with_trace_extra
from features.recommend.profile_store import ProfileStore, load_presented_names
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
    picks: Sequence[int | str],
    scales: Sequence[int] | None,
    presented: Sequence[FlavorVector],
    now: datetime,
    cuisines: Sequence[str] = (),
    presented_names: Sequence[str] = (),
) -> TasteProfile:
    """온보딩 응답을 취향 원본으로 바꿉니다. 범위 밖 인덱스와 척도는 거부합니다.

    조용히 받거나 잘라 넣으면 다른 음식의 맛이 취향이 되고 에러는 나지 않습니다. 같은 음식을
    두 번 고른 것은 한 번으로 둡니다 - 평균이 그쪽으로 두 배 기울 이유가 없습니다. 척도는
    여기서 0~1 로 옮기며, 이 변환은 저장소에도 엔진에도 없습니다.

    음식 유형은 맛 6축과 섞지 않습니다. 고른 유형의 평균 맛을 취향에 더하면 "한식을 좋아함"이
    "짜고 매운 것을 좋아함"으로 번역되어, 유형을 고른 것만으로 맛 취향이 통째로 움직입니다.
    """
    # 이름을 안 넘기면 시드의 것을 씁니다. 6축과 이름은 같은 파일에서 나오므로
    # 길이가 어긋나면 배선이 잘못된 것이라 조용히 넘기지 않고 멈춥니다.
    names = tuple(presented_names) or load_presented_names()
    if picks and len(names) != len(presented):
        raise ValueError(
            f"제시 목록의 이름과 6축의 개수가 다릅니다: {len(names)} != {len(presented)}"
        )
    indexes = tuple(dict.fromkeys(_pick_index(p, names, len(presented)) for p in picks))
    if len(indexes) != len(picks):
        bump("persona_pick_duplicate", len(picks) - len(indexes))
    if 0 < len(indexes) < MIN_PICKS:
        bump("persona_picks_under_min")
    return TasteProfile(
        user_id=user_id,
        picks=tuple(names[i] for i in indexes),
        pick_flavors=tuple(presented[i] for i in indexes),
        scales=None if scales is None else _normalized_scales(scales),
        cuisines=_normalized_cuisines(cuisines),
        updated_at=now,
    )


def _normalized_cuisines(cuisines: Sequence[str]) -> tuple[str, ...]:
    """라벨로 온 유형을 코드로 맞춥니다. 모르는 값은 거부하고 중복은 한 번으로 둡니다.

    가까운 유형으로 추측하지 않습니다 - 고르지 않은 유형의 음식이 목록에 올라오면
    사용자는 그것을 자기가 고른 결과로 읽습니다.
    """
    seen: list[str] = []
    unknown: list[str] = []
    for raw in cuisines:
        code = normalize_cuisine(str(raw))
        if code is None or code not in ONBOARDING_CUISINES:
            unknown.append(str(raw))
        elif code not in seen:
            seen.append(code)
    if unknown:
        bump("persona_cuisine_unknown", len(unknown))
        raise ValueError(f"모르는 음식 유형입니다: {unknown}")
    return tuple(seen)


def _pick_index(pick: int | str, names: Sequence[str], count: int) -> int:
    """고른 음식을 제시 목록의 자리로 바꿉니다. 이름과 인덱스를 둘 다 받습니다.

    이름이 정본입니다. 인덱스는 시뮬·검사가 쓰던 모양이라 계속 받습니다.
    어느 쪽이든 목록 밖이면 거부합니다 — 짐작해서 채우면 고르지 않은 음식이
    그 사용자의 취향이 되고 에러는 나지 않습니다.
    """
    if isinstance(pick, str):
        if pick not in names:
            bump("persona_pick_unknown_name")
            raise ValueError(f"제시 목록에 없는 음식입니다: {pick!r}")
        return names.index(pick)
    index = int(pick)
    if not 0 <= index < count:
        bump("persona_pick_out_of_range")
        raise ValueError(f"제시 목록 밖의 인덱스입니다: {index}")
    return index


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
    #: 같은 목록의 이름. 고른 음식을 이름으로 적기 위해 함께 듭니다.
    presented_names: Sequence[str] = field(default_factory=load_presented_names)

    def save_onboarding(
        self,
        user_id: int,
        picks: Sequence[int | str],
        scales: Sequence[int] | None,
        now: datetime,
        cuisines: Sequence[str] = (),
    ) -> Persona:
        """고른 음식과 척도 원본을 저장하고 페르소나를 돌려줍니다.

        이미 있는 이벤트는 지키므로 온보딩을 다시 해도 이력이 사라지지 않습니다. 저장 전에
        이벤트를 정책 상한으로 잘라냅니다. 음식 유형은 이벤트로 갱신되지 않으므로 다시
        온보딩하면 그때 고른 것으로 바뀝니다.
        """
        profile = onboarding_profile(
            user_id, picks, scales, self.presented, now, cuisines, self.presented_names
        )
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

        무시·저장 취소처럼 무게가 0 인 종류, 레시피 없이 온 조리·저장·클릭·별점, 범위 밖 별점,
        맛을 모르는 레시피, 한 배치 안의 같은 이벤트는 저장하지 않고 **셉니다.** 삼키기만 하면
        취향이 안 쌓이는 것을 아무도 모릅니다. 검색처럼 레시피가 없는 것이 정상인 종류는 지나갑니다.

        시각은 이벤트가 `occurred_at` 을 실었으면 그것이고, 없으면 서버 수신 시각 `now` 입니다.
        사용자마다
        읽고-합치고-쓰기를 하며 프로세스 안에서는 잠금으로 직렬화합니다. 프로세스가 여럿이면
        나중 쓰기가 앞 쓰기를 덮습니다 - 실 DB 로 옮기면(M-14) 사라지는 제약입니다.
        """
        persona_engine.require_aware(now, "now")
        added: dict[int, list[TasteEvent]] = {}
        seen: set[tuple[int, int, EventType, UUID | None]] = set()
        for event in events:
            if event.recipe_id is None:
                # 검색·노출처럼 레시피가 없는 것이 정상인 종류는 지나갑니다. 조리·저장·클릭·별점이
                # 레시피 없이 오면 잘못된 이벤트로 셉니다. 조용히 버리면 취향이 안 쌓이는 이유를
                # 아무도 모릅니다.
                if _carries_taste(event.event_type):
                    bump("persona_event_invalid")
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

        원본 파일이 깨져 있거나(ValueError) 저장소를 읽을 수 없으면(OSError) 추천을
        실패시키지 않고 취향 없는 사용자로 다루되 **셉니다.** 저장소는 예외를 올리고(조용한
        빈 취향 금지), 서빙은 여기서 받습니다. 둘은 이름을 달리 세어 원인을 가릅니다.
        """
        try:
            with _PROFILE_LOCK:
                profile = self.store.load(user_id)
        except ValueError:
            bump("persona_profile_unreadable")
            return persona_engine.cold_persona()
        except OSError:
            bump("persona_store_error")
            return persona_engine.cold_persona()
        if profile is None:
            bump("persona_missing")
            return persona_engine.cold_persona()
        return persona_engine.derive_persona(profile, now, self.policy)


def _carries_taste(kind: EventType) -> bool:
    """레시피가 있어야 뜻이 있는 종류인가. 별점은 값에 따라 무게가 달라 따로 봅니다."""
    return kind is EventType.RATING or persona_engine.kind_weight(kind, None) > 0.0


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
    # 발생 시각이 왔으면 그것을, 없으면 수신 시각을. 계약이 시간대를 강제하므로 여기서는 믿습니다.
    return TasteEvent(
        recipe_id=recipe_id,
        kind=event.event_type,
        at=event.occurred_at or now,
        flavor=flavor,
        value=event.value,
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
    *,
    top_k: int = 20,
    weights: Mapping[str, float] | None = None,
    rng_seed: int = 0,
    max_missing_final: int | None = None,
    serving_mode: str = "real",
    batch_versions: Mapping[str, str | None] | None = None,
) -> RankingResult:
    """② 점수 계산 → ③ 재정렬. 정책으로 거르지는 않습니다 - 그것은 ① 의 일입니다.

    난수원은 추적에 적는 `rng_seed` 로 여기서 만듭니다. 호출자가 난수원을 따로 넘기면 추적의
    시드가 실제 난수와 무관해져 로그가 "재현 가능" 이라고 거짓말을 합니다.

    ① 이 준 후보 가운데 레시피 피처가 없는 것과 중복은 점수를 매기지 않고 세어 추적의
    `filters` 에 남깁니다. 피처가 없는 후보는 갖춘 재료 하나만으로 만점이 되어 1위로 나갑니다.

    같은 요리의 판본은 재정렬 앞에서 하나로 줄입니다(`engine/dish.py`). 재정렬과 탐색 규격이
    **같은 목록**을 보도록 여기서 줄입니다 — 재정렬 안에서 줄이면 추적의 부족분이 실제와 갈립니다.
    """
    _validate_weights(weights)
    rng = random.Random(rng_seed)  # noqa: S311  # 재현용 시드 RNG. 암호 용도가 아닙니다
    usable, filters = _rankable(candidates, recipes)
    started = perf_counter()
    scored = score.score_all(
        usable, recipes, ctx, corpus, policy, weights, max_missing=max_missing_final
    )
    ranking_ms = _elapsed(started)

    started = perf_counter()
    # `score_all` 이 순위 순서로 돌려주므로 남는 판본은 이 사용자에게 점수가 가장 높은 것입니다.
    distinct = dish.collapse_versions(scored, recipes, corpus.ingredient_names, policy.max_per_dish)
    items = rerank.rerank(distinct, recipes, ctx, corpus, policy, rng, top_k=top_k, weights=weights)
    rerank_ms = _elapsed(started)

    n_explore = sum(1 for item in items if item.is_exploration)
    # 재정렬이 쓴 것과 같은 규격입니다. 부족분과 폴백을 여기서 따로 정하면 로그가 갈라집니다.
    spec = rerank.exploration_spec(distinct, ctx, policy, len(items))
    # 후보가 모자라 탐색이 줄어든 만큼입니다. 0 이 아니면 ① 이 덜 가져온 것입니다.
    shortfall = max(0, spec.count - n_explore)
    if shortfall:
        bump("explore_shortfall", shortfall)
    # 어느 배치 산출물 위에서 나온 추천인지. 호출자가 `repository.load_batch_versions()` 로
    # 읽어 넘기고, 목업 서빙 동안은 None 으로 키만 실립니다 — 키 자체가 소급 불가입니다.
    versions = batch_versions or {}
    params = policy.trace_params(
        top_k=top_k,
        n_explore=n_explore,
        rng_seed=rng_seed,
        max_missing_final=(policy.max_missing if max_missing_final is None else max_missing_final),
        serving_mode=serving_mode,
        feature_version=versions.get("feature_version"),
        cluster_version=versions.get("cluster_version"),
    )
    params = with_trace_extra(
        params,
        {
            **_persona_params(ctx),
            **_explore_params(spec, policy),
            **_cuisine_params(ctx, policy, items, recipes),
            # 어느 손잡이·가중치로 만든 목록인지. policy_id 는 값이 바뀌어도 그대로입니다.
            "policy_fingerprint": policy.fingerprint(None if weights is None else dict(weights)),
        },
    )
    stages = [
        StageInfo(
            name=Stage.RANKING,
            in_count=len(candidates),
            out_count=len(scored),
            latency_ms=ranking_ms,
            strategy="linear-weighted",
            filters=filters,
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
                "same_dish": len(scored) - len(distinct),
                "mmr_or_cap": max(0, len(distinct) - len(items)),
                "explore_shortfall": shortfall,
            },
            params=params,
            exploration_items=[item.recipe_id for item in items if item.is_exploration],
        ),
    ]
    return RankingResult(items=items, scored=scored, stages=stages)


def _validate_weights(weights: Mapping[str, float] | None) -> None:
    """디버거가 넘기는 가중치 덮어쓰기를 검사합니다.

    오타 키는 조용히 그 피처의 몫을 없애고, 음수는 0 으로 읽히며, 합이 0 이면 전원 0 점인
    목록이 그대로 나갑니다. 셋 다 에러가 없습니다.
    """
    if weights is None:
        return
    unknown = sorted(set(weights) - set(FEATURE_KEYS))
    if unknown:
        raise ValueError(f"FEATURE_KEYS 에 없는 가중치입니다: {unknown}")
    negative = sorted(key for key, weight in weights.items() if weight < 0.0)
    if negative:
        raise ValueError(f"음의 가중치는 쓸 수 없습니다: {negative}")
    if sum(weights.values()) <= 0.0:
        raise ValueError("가중치 합이 0 이면 점수를 매길 수 없습니다")


def _rankable(
    candidates: Sequence[Candidate], recipes: Mapping[int, RecipeFeature]
) -> tuple[list[Candidate], dict[str, int]]:
    """점수를 매길 수 있는 후보만 남기고 뺀 이유를 셉니다. 같은 레시피는 먼저 온 것만 둡니다."""
    usable: list[Candidate] = []
    seen: set[int] = set()
    filters = {"recipe_feature_missing": 0, "candidate_duplicate": 0}
    for candidate in candidates:
        if candidate.recipe_id in seen:
            filters["candidate_duplicate"] += 1
            continue
        seen.add(candidate.recipe_id)
        if candidate.recipe_id not in recipes:
            filters["recipe_feature_missing"] += 1
            continue
        usable.append(candidate)
    for key, count in filters.items():
        if count:
            bump(key, count)
    return usable, {key: count for key, count in filters.items() if count}


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


def _explore_params(spec: rerank.ExplorationSpec, policy: RankingPolicy) -> dict[str, object]:
    """군집 없이 균등으로 폴백했으면 로그에 남기고 셉니다.

    `uniform_share` 는 동결 키라 덮지 않습니다. 평가는 `explore_fallback` 이 있으면 그 요청의
    실효 균등 비율을 1.0 으로 읽습니다.
    """
    if spec.uniform_share == policy.uniform_share:
        return {}
    bump("explore_uniform_fallback")
    return {"explore_fallback": "uniform"}


def _cuisine_params(
    ctx: UserContext,
    policy: RankingPolicy,
    items: Sequence[RankedItem],
    recipes: Mapping[int, RecipeFeature],
) -> dict[str, object]:
    """유형 슬롯이 몇 칸이었나. 고른 유형이 없으면 아무것도 싣지 않습니다.

    칸 수만으로는 "이미 목록에 있어서 0" 과 "후보에 그 유형이 한 건도 없어서 0" 이 구분되지
    않습니다. 그래서 목록에 끝내 없는 유형을 함께 남깁니다. 그 값이 계속 차 있으면 ① 이 덜
    가져왔거나 레시피 쪽 `cuisine_family` 가 비어 있다는 뜻입니다 - 실 DB 는 09-17 규칙
    배정으로 61.7% 만 차 있어(`PENDING_DATA_FEATURES`) 이 칸이 유일한 신호입니다.
    """
    if not ctx.preferred_cuisines:
        return {}
    served = {recipes[item.recipe_id].cuisine for item in items if item.recipe_id in recipes}
    unmet = sorted(family for family in ctx.preferred_cuisines if family not in served)
    params: dict[str, object] = {
        "n_cuisine": sum(1 for item in items if item.is_cuisine_slot),
        "cuisine_slot_ratio": policy.cuisine_slot_ratio,
        "preferred_cuisines": ",".join(sorted(ctx.preferred_cuisines)),
    }
    if unmet:
        bump("cuisine_unmet", len(unmet))
        params["cuisine_unmet"] = ",".join(unmet)
    return params


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
