"""시드를 엔진의 입력 모델로 바꾸고, 한 사용자를 서빙해 요약합니다.

`scenario_engine.py` 가 이것으로 시나리오를 돌립니다. 둘을 나눈 선은 "시드 → 엔진 입력"과
"입력으로 시나리오를 돌리고 판정" 입니다. 냉장고와 임박 판정은 A 트랙 SQL 함수
`user_pantry_ids` · `effective_expiry` 의 규칙을 따릅니다 - staple 합집합, 유저 입력이 없으면
구매일 + 기본 소비기한, D-3 이내가 임박(지난 것 포함).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import ModuleType

from sim_seed import (
    ROOT,
    Catalog,
    ShelfLife,
    SimUser,
    allergy_ids,
    effective_expiry,
)

from features.recommend import service
from features.recommend.engine import persona as persona_engine
from features.recommend.engine.context import UserContext, UserHistory, build_context
from features.recommend.engine.persona import Persona, TasteEvent
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import EventType
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate, RankedItem

PRESENTED_PATH = ROOT / "seeds" / "onboarding_recipes.yaml"
TOP_K = 20
#: `f_expiring(p_horizon)` 의 기본값. days_left 가 이 값 이하면 임박이며, 이미 지난 것도 셉니다.
EXPIRING_HORIZON_DAYS = 3
#: `UserHistory.cooked_recipe_ids` 의 창(최근 14일 조리)과 사유 문구에 쓰는 최근 조리 수.
COOKED_WINDOW = timedelta(days=14)
COOKED_KEEP = 20
#: `user_preference.skill_level` 은 CHECK 1~3 이고 엔진의 `skill_level` 은 0~1 입니다.
SKILL_MIN, SKILL_MAX = 1, 3
#: 상위 몇 개로 목록의 겹침을 재는가. 시나리오와 요약이 같은 값을 봐야 합니다.
OVERLAP_K = 10


@dataclass(frozen=True)
class Summary:
    user_id: int
    group: str
    sim_mode: str
    sim_events: int
    source: str
    mode: str
    behavior_weight: float
    pantry: int
    unmapped: int
    expiring: int
    candidates: int
    stage: str
    served: int
    explore: int
    shortfall: int
    violations: tuple[str, ...]
    top: tuple[int, ...]
    #: 온보딩에서 고른 음식 유형과, 그중 목록에 한 건도 안 나온 것.
    cuisines: tuple[str, ...]
    cuisine_slots: int
    cuisine_unmet: tuple[str, ...]


# ─────────────────────────────────────────────────────────────────
# 시드 → 엔진 입력
# ─────────────────────────────────────────────────────────────────
def persona_of(
    user: SimUser,
    cat: Catalog,
    presented: Sequence[FlavorVector],
    now: datetime,
    policy: RankingPolicy,
) -> Persona:
    """온보딩 원본과 `now` 까지의 이벤트로 페르소나를 만듭니다. 모르는 RCP 는 건너뜁니다."""
    profile = service.onboarding_profile(user.user_id, user.picks, user.scales, presented, now)
    events = tuple(
        TasteEvent(
            recipe_id=cat.by_code[e.code],
            kind=e.kind,
            at=e.at,
            flavor=cat.recipes[cat.by_code[e.code]].flavor_vec,
        )
        for e in user.events
        if e.at <= now and e.code in cat.by_code
    )
    kept = persona_engine.prune_events(events, now, policy)
    return persona_engine.derive_persona(replace(profile, events=kept), now, policy)


def context_of(
    user: SimUser, cat: Catalog, shelf: ShelfLife, made: Persona, now: datetime
) -> UserContext:
    """`now` 시점의 냉장고와 이력. 노출 로그는 시드에 없어 `recent_recipe_ids` 는 비웁니다."""
    held = [row for row in user.pantry if row.added <= now]
    own = {cat.ingredient_ids[r.name] for r in held if r.name in cat.ingredient_ids}
    expiring: set[int] = set()
    for row in held:
        ingredient = cat.ingredient_ids.get(row.name)
        if ingredient is None or ingredient in cat.staple_ids:
            continue
        expiry = effective_expiry(row, shelf)
        if expiry is not None and (expiry - now.date()).days <= EXPIRING_HORIZON_DAYS:
            expiring.add(ingredient)
    past = [e for e in user.events if e.at <= now and e.code in cat.by_code]
    clicks = [(cat.by_code[e.code], e.at) for e in past if e.kind is EventType.CLICK]
    cooks = [(cat.by_code[e.code], e.at) for e in past if e.kind is EventType.COOK]
    latest = cooks[-COOKED_KEEP:]
    seen = Counter(cat.clusters[rid] for rid, _ in clicks)
    hits = Counter(cat.clusters[rid] for rid, _ in cooks)
    history = UserHistory(
        cooked_recipe_ids=frozenset(rid for rid, at in cooks if now - at <= COOKED_WINDOW),
        cooked_ingredient_sets=tuple(cat.recipes[rid].all_ids for rid, _ in latest),
        cooked_titles=tuple(cat.recipes[rid].title for rid, _ in latest),
        cluster_seen={c: max(seen[c], hits[c]) for c in set(seen) | set(hits)},
        cluster_hits=dict(hits),
    )
    skill = None
    if user.skill_level is not None:
        skill = (user.skill_level - SKILL_MIN) / (SKILL_MAX - SKILL_MIN)
    return build_context(
        user_id=user.user_id,
        persona=made,
        pantry_ids=sorted(own | cat.staple_ids),
        expiring_ids=sorted(expiring),
        history=history,
        max_cook_minutes=user.max_cook_minutes,
        preferred_cuisines=[c for c in user.cuisines if c in cat.cuisines],
        skill_level=skill,
    )


def serve(
    ev: ModuleType, cat: Catalog, ctx: UserContext, banned: frozenset[int], policy: RankingPolicy
) -> tuple[service.RankingResult, str, list[Candidate]]:
    candidates, stage, max_missing = ev.retrieve_with_fallback(
        cat.recipes, cat.clusters, ctx, policy, TOP_K, banned
    )
    result = service.rank_candidates(
        candidates,
        cat.recipes,
        ctx,
        cat.corpus,
        policy,
        top_k=TOP_K,
        rng_seed=ctx.user_id,
        max_missing_final=max_missing,
    )
    return result, stage, candidates


def violations(
    items: Sequence[RankedItem], cat: Catalog, ctx: UserContext, banned: frozenset[int]
) -> tuple[str, ...]:
    """깨지면 안 되는 것들. 이름을 돌려주고, 비어 있으면 통과입니다."""
    served = [item.recipe_id for item in items]
    found: list[str] = []
    if any(cat.recipes[rid].all_ids & banned for rid in served):
        found.append("allergy")
    if len(served) != len(set(served)):
        found.append("duplicate")
    if any(i.propensity is None or not 0.0 < i.propensity <= 1.0 for i in items):
        found.append("propensity")
    if any(not i.reason or "{" in i.reason for i in items):
        found.append("reason")
    if [i.final_rank for i in items] != list(range(1, len(items) + 1)):
        found.append("rank")
    picked = [i for i in items if i.is_cuisine_slot]
    if any(
        i.is_exploration
        or i.propensity != 1.0
        or cat.recipes[i.recipe_id].cuisine not in ctx.preferred_cuisines
        for i in picked
    ):
        found.append("cuisine_slot")
    cap = ctx.max_cook_minutes
    personal = [i for i in items if not i.is_exploration]
    if cap is not None and any(
        (cat.recipes[i.recipe_id].cook_minutes or 0) > cap for i in personal
    ):
        found.append("cook_cap")
    return tuple(found)


@dataclass(frozen=True)
class World:
    """모든 시나리오가 함께 쓰는 것."""

    ev: ModuleType
    cat: Catalog
    shelf: ShelfLife
    presented: Sequence[FlavorVector]
    policy: RankingPolicy
    fallback_now: datetime

    def now_of(self, user: SimUser) -> datetime:
        last = user.last_activity()
        return self.fallback_now if last is None else last + timedelta(hours=1)

    def run(self, user: SimUser, now: datetime) -> tuple[service.RankingResult, str, int, Persona]:
        made = persona_of(user, self.cat, self.presented, now, self.policy)
        ctx = context_of(user, self.cat, self.shelf, made, now)
        banned = allergy_ids(user, self.cat)
        result, stage, candidates = serve(self.ev, self.cat, ctx, banned, self.policy)
        return result, stage, len(candidates), made

    def title(self, recipe_id: int) -> str:
        return self.cat.recipes[recipe_id].title


def summarize(world: World, user: SimUser) -> Summary:
    now = world.now_of(user)
    made = persona_of(user, world.cat, world.presented, now, world.policy)
    ctx = context_of(user, world.cat, world.shelf, made, now)
    banned = allergy_ids(user, world.cat)
    result, stage, candidates = serve(world.ev, world.cat, ctx, banned, world.policy)
    return Summary(
        user_id=user.user_id,
        group=user.group,
        sim_mode=user.sim_mode,
        sim_events=user.sim_events,
        source=made.prior_source.value,
        mode=made.mode.value,
        behavior_weight=made.behavior_weight,
        pantry=len(ctx.pantry_ids - world.cat.staple_ids),
        unmapped=sum(1 for r in user.pantry if r.name not in world.cat.ingredient_ids),
        expiring=len(ctx.expiring_ids),
        candidates=len(candidates),
        stage=stage,
        served=len(result.items),
        explore=sum(item.is_exploration for item in result.items),
        shortfall=result.stages[-1].dropped.get("explore_shortfall", 0),
        violations=violations(result.items, world.cat, ctx, banned),
        top=tuple(item.recipe_id for item in result.items[:OVERLAP_K]),
        cuisines=tuple(sorted(ctx.preferred_cuisines)),
        cuisine_slots=sum(item.is_cuisine_slot for item in result.items),
        cuisine_unmet=unmet_cuisines(result.items, world.cat, ctx),
    )


def unmet_cuisines(items: Sequence[RankedItem], cat: Catalog, ctx: UserContext) -> tuple[str, ...]:
    """고른 유형 가운데 목록에 한 건도 안 나온 것. 후보에 그 유형이 없었다는 뜻입니다."""
    served = {cat.recipes[item.recipe_id].cuisine for item in items}
    return tuple(sorted(family for family in ctx.preferred_cuisines if family not in served))
