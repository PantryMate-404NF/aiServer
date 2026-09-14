"""시뮬 시드를 DB 없이 엔진에 직접 넣어 이용 시나리오를 돌립니다.

실행: uv run python scripts/sim/scenario_engine.py [--seed-dir deploy/seed/sim] [--limit 0]

`scenario_run.py` 는 HTTP 로 서버를 부르는데, 라우터가 목업이라 시드가 응답에 닿지 않습니다
(DB 전환 점검표 M-01·M-03). 이 스크립트는 그 두 항목이 할 일을 파이썬 안에서 대신합니다.
시드 SQL 을 읽어 온보딩 원본·행동 이벤트·냉장고·알러지·선호를 엔진의 입력 모델로 만들고,
레시피는 Mock 카탈로그 120건을 published 레시피처럼 씁니다(읽기는 `sim_seed.py`). 냉장고와
임박 판정은 A 트랙 SQL 함수 `user_pantry_ids` · `effective_expiry` 의 규칙을 따릅니다 -
staple 합집합, 유저 입력이 없으면 구매일 + 기본 소비기한, D-3 이내가 임박(지난 것 포함).

순서
  [1] 시드 전원 - 집단별 페르소나 출처·모드, 후보 수, 완화 단계, 탐색 칸, 불변식
  [2] 이벤트가 가장 무거운 A 집단 유저 - 이력을 시간순으로 따라가며 콜드 → 웜 전환을 봅니다
  [3] 같은 유저 - 기준 목록 → cook 5건 추가 → 페르소나·상위 10 변동
  [4] 같은 유저 - 아직 없는 재료를 임박으로 추가 → 그 재료를 쓰는 레시피 수 변동
  [5] 같은 시드 두 번 실행의 동일성, onboarding 만 있는 유저와의 상위 10 겹침

'지금' 은 유저마다 마지막 활동(이벤트·냉장고 등록) 한 시간 뒤입니다. 그래야 감쇠와 임박
판정이 시드의 달력 위에서 돕니다. 종료 코드로 판정합니다. 0 = 불변식(알러지 0건 · 중복 없음 ·
노출 확률 (0,1] · 사유 채움 · 조리시간 상한 · 순위 연속) 전부 통과, 재현 동일, 콜드 → 웜 전환,
행동과 냉장고가 목록을 움직임. 이 시드는 임의 데이터라 수치를 '실측' 으로 쓰지 않습니다.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

from sim_seed import (
    KST,
    ROOT,
    Catalog,
    PantryRow,
    ShelfLife,
    SimEvent,
    SimUser,
    allergy_ids,
    effective_expiry,
    load_catalog,
    load_eval,
    load_shelf_life,
    load_users,
)

from features.recommend import service
from features.recommend.engine import persona as persona_engine
from features.recommend.engine.context import UserContext, UserHistory, build_context
from features.recommend.engine.persona import Persona, TasteEvent
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import EventType, UserMode
from features.recommend.policy import RankingPolicy
from features.recommend.profile_store import load_presented_flavors
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
#: [3] 의 자극. `scenario_run.py` 와 같은 조리 수입니다. [4] 의 재료는 유저마다 고릅니다.
EXTRA_COOKS = 5
EXTRA_INGREDIENTS = 2
#: [2] 에서 페르소나를 다시 재는 이벤트 수. 마지막 이벤트 시점은 항상 더합니다.
CHECKPOINTS = (0, 1, 5, 10, 20, 50)
TOP_SHOW = 5
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
    cap = ctx.max_cook_minutes
    personal = [i for i in items if not i.is_exploration]
    if cap is not None and any(
        (cat.recipes[i.recipe_id].cook_minutes or 0) > cap for i in personal
    ):
        found.append("cook_cap")
    return tuple(found)


# ─────────────────────────────────────────────────────────────────
# 시나리오
# ─────────────────────────────────────────────────────────────────
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
    )


def print_group(group: str, part: Sequence[Summary]) -> None:
    with_candidates = [s for s in part if s.candidates > 0]
    print(f"  {group}: {len(part)}명 · 냉장고 있음 {sum(1 for s in part if s.pantry)}명", end="")
    print(f" · 후보 있음 {len(with_candidates)}명", end="")
    print(f" · 임박 재료 있음 {sum(1 for s in part if s.expiring)}명")
    print(f"     시드 computed_from  {dict(Counter(s.sim_mode for s in part))}")
    print(f"     엔진 출처/모드      {dict(Counter(f'{s.source}/{s.mode}' for s in part))}")
    for sim_mode in sorted({s.sim_mode for s in part}):
        same = [s for s in part if s.sim_mode == sim_mode]
        weights = sorted(s.behavior_weight for s in same)
        print(f"     시드 {sim_mode:10s} → 엔진 {dict(Counter(s.mode for s in same))}", end="")
        print(f" · 행동 무게 중위 {statistics.median(weights):.1f} 최대 {weights[-1]:.1f}")
    if with_candidates:
        served = statistics.fmean(s.served for s in with_candidates)
        print(f"     노출 수 평균 {served:.1f}", end="")
        print(f" · 완화 단계 {dict(Counter(s.stage for s in with_candidates))}")
        explores = dict(sorted(Counter(s.explore for s in with_candidates).items()))
        print(f"     탐색 칸 {explores}", end="")
        print(f" · 부족분 합 {sum(s.shortfall for s in with_candidates)}")


def print_everyone(summaries: Sequence[Summary]) -> bool:
    print(f"\n=== [1] 전원 {len(summaries)}명 · 각자 마지막 활동 한 시간 뒤 기준")
    for group in sorted({s.group for s in summaries}):
        print_group(group, [s for s in summaries if s.group == group])
    unmapped = sum(s.unmapped for s in summaries)
    total = unmapped + sum(s.pantry for s in summaries)
    print(f"  냉장고 재료 {total}건 중 Mock 카탈로그에 이름이 없는 것 {unmapped}건", end="")
    print(" (실 DB 에서는 전부 이름으로 조인됩니다)")
    broken = [s for s in summaries if s.violations]
    kinds = dict(Counter(v for s in broken for v in s.violations))
    print(f"  불변식 위반 {len(broken)}명 {kinds}")
    for s in broken[:TOP_SHOW]:
        print(f"     user {s.user_id}: {s.violations}")
    return not broken


def timeline(world: World, user: SimUser) -> tuple[str, str]:
    """이벤트를 시간순으로 따라가며 페르소나가 어떻게 바뀌는지. 처음과 끝의 모드를 돌려줍니다."""
    events = [e for e in user.events if e.code in world.cat.by_code]
    print(f"\n=== [2] user {user.user_id} ({user.group}, 시드 {user.sim_mode} {user.sim_events}건)")
    print(f"    온보딩 picks={user.picks} scales={user.scales}", end="")
    print(f" · 냉장고 {len(user.pantry)}건 · 알러지 {user.allergy_groups or '-'}", end="")
    print(f" · 상한 {user.max_cook_minutes}분")
    modes: list[str] = []
    for n in sorted({*(c for c in CHECKPOINTS if c <= len(events)), len(events)}):
        hour = timedelta(hours=1)
        moment = events[0].at - hour if n == 0 else events[n - 1].at + hour
        trimmed = replace(user, events=tuple(events[:n]))
        result, stage, n_candidates, made = world.run(trimmed, moment)
        personal = [i for i in result.items if not i.is_exploration][:3]
        titles = ", ".join(world.title(i.recipe_id) for i in personal)
        print(f"    이벤트 {n:3d}건 · {moment:%Y-%m-%d}: {made.mode.value:10s}", end="")
        print(f" 무게 {made.behavior_weight:6.2f} · 후보 {n_candidates:3d} {stage:14s} | {titles}")
        modes.append(made.mode.value)
    return modes[0], modes[-1]


def scenario_behavior(world: World, user: SimUser, now: datetime) -> bool:
    """개인화 상위 5건을 지금 조리한 것으로 넣으면 페르소나와 목록이 움직여야 합니다."""
    before, _, _, made = world.run(user, now)
    top_before = [i.recipe_id for i in before.items[:OVERLAP_K]]
    print(f"\n=== [3] user {user.user_id} · cook {EXTRA_COOKS}건 추가")
    print(f"    기준 페르소나 {made.prior_source.value}/{made.mode.value}", end="")
    print(f" 무게 {made.behavior_weight:.2f} 취향 {world.ev.fmt(made.vec)}")
    for item in before.items[:TOP_SHOW]:
        flag = "X" if item.is_exploration else " "
        print(f"     {item.final_rank:2d} {flag} {item.score:.3f}", end="")
        print(f" {world.title(item.recipe_id)} | {item.reason}")
    cooked = [i.recipe_id for i in before.items if not i.is_exploration][:EXTRA_COOKS]
    extra = tuple(
        SimEvent(code=world.cat.code_of[rid], kind=EventType.COOK, at=now) for rid in cooked
    )
    later = now + timedelta(hours=1)
    after, _, _, after_made = world.run(replace(user, events=(*user.events, *extra)), later)
    top_after = [i.recipe_id for i in after.items[:OVERLAP_K]]
    moved = sum(1 for a, b in zip(top_before, top_after, strict=False) if a != b)
    delta = max(
        abs((after_made.vec[i] or 0.0) - (made.vec[i] or 0.0)) for i in range(len(made.vec))
    )
    still = [world.title(rid) for rid in cooked if rid in top_after]
    print(f"    → 무게 {made.behavior_weight:.2f} → {after_made.behavior_weight:.2f}", end="")
    print(f" · 취향 최대 변화 {delta:.3f} · 상위 {OVERLAP_K} 중 {moved} 자리 변동")
    print(f"    방금 조리한 {len(cooked)}건 중 상위 {OVERLAP_K} 에 남은 것: {still or '없음'}")
    return moved > 0 or delta > 0.0


def stimulus_names(world: World, user: SimUser, now: datetime) -> list[str]:
    """[4] 의 자극. 유저가 아직 없는 재료 중 카탈로그(상한·알러지 안)에서 가장 많이 쓰이는 것.

    `scenario_run.py` 처럼 이름을 고정하면 이미 그 재료가 임박한 유저에게는 자극이 아닙니다.
    """
    held = {row.name for row in user.pantry if row.added <= now}
    banned = allergy_ids(user, world.cat)
    cap = user.max_cook_minutes
    usage: Counter[int] = Counter()
    for recipe in world.cat.recipes.values():
        if recipe.all_ids & banned or (cap is not None and (recipe.cook_minutes or 0) > cap):
            continue
        usage.update(recipe.all_ids - world.cat.staple_ids)
    name_of = {i: n for n, i in world.cat.ingredient_ids.items()}
    fresh = [name_of[i] for i, _ in usage.most_common() if name_of[i] not in held]
    return fresh[:EXTRA_INGREDIENTS]


def scenario_expiring(world: World, user: SimUser, now: datetime) -> bool:
    """임박 재료를 냉장고에 더하면 그 재료를 쓰는 레시피가 목록에 더 올라와야 합니다.

    "임박 재료를 하나라도 쓰는 비율" 은 기준이 못 됩니다 - 이미 임박 재료가 넷인 유저는
    다섯째를 더해도 그 비율이 흔들릴 뿐입니다. 더한 재료 자체를 쓰는 레시피 수를 봅니다.
    """
    names = stimulus_names(world, user, now)
    added_ids = frozenset(world.cat.ingredient_ids[n] for n in names)
    soon = now.date() + timedelta(days=2)
    added = tuple(PantryRow(name=n, purchased=now.date(), expires=soon, added=now) for n in names)
    later = now + timedelta(hours=1)
    richer = replace(user, pantry=(*user.pantry, *added))
    before, _, _, made = world.run(user, now)
    after, _, _, _ = world.run(richer, later)
    ctx_before = context_of(user, world.cat, world.shelf, made, now)
    ctx_after = context_of(richer, world.cat, world.shelf, made, later)

    def uses(result: service.RankingResult, ids: frozenset[int]) -> int:
        personal = [i for i in result.items if not i.is_exploration]
        return sum(1 for i in personal if world.cat.recipes[i.recipe_id].all_ids & ids)

    cap = user.max_cook_minutes
    usable = sum(
        1
        for r in world.cat.recipes.values()
        if r.all_ids & added_ids and (cap is None or (r.cook_minutes or 0) <= cap)
    )
    hits_before, hits_after = uses(before, added_ids), uses(after, added_ids)
    any_before = uses(before, ctx_before.expiring_ids)
    any_after = uses(after, ctx_after.expiring_ids)
    measured = any(i.features.get("f_expiring") is not None for i in after.items)
    print(f"\n=== [4] user {user.user_id} · 임박 재료 {names} 추가 (아직 없는 재료 중 최다 사용)")
    print(f"    임박 재료 {len(ctx_before.expiring_ids)} → {len(ctx_after.expiring_ids)}종", end="")
    print(f" · 더한 재료를 쓰는 개인화 레시피 {hits_before} → {hits_after}건", end="")
    print(f" (카탈로그에서 조리시간 상한 안인 것 {usable}건)")
    print(f"    임박 재료를 하나라도 쓰는 개인화 레시피 {any_before} → {any_after}건", end="")
    print(f" · f_expiring 측정 {measured}")
    return measured and hits_after > hits_before


def scenario_repeat(world: World, user: SimUser, now: datetime, peers: Sequence[Summary]) -> bool:
    first, _, _, _ = world.run(user, now)
    second, _, _, _ = world.run(user, now)
    same = [i.model_dump() for i in first.items] == [i.model_dump() for i in second.items]
    print(f"\n=== [5] 같은 시드 두 번 실행 동일: {same}")
    top = {i.recipe_id for i in first.items[:OVERLAP_K]}
    cold = next((s for s in peers if s.mode == UserMode.COLD.value and s.candidates >= TOP_K), None)
    if cold is not None:
        overlap = len(top & set(cold.top))
        print(f"    onboarding 만 있는 user {cold.user_id} 의 상위 {OVERLAP_K} 과 겹침", end="")
        print(f" {overlap}/{OVERLAP_K}")
    return same


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-dir", type=Path, default=ROOT / "deploy" / "seed" / "sim")
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N 명만 (0 = 전원)")
    args = parser.parse_args()

    ev = load_eval()
    cat = load_catalog(ev)
    users = load_users(args.seed_dir)
    if args.limit:
        users = dict(list(users.items())[: args.limit])
    last = max(
        (m for u in users.values() if (m := u.last_activity()) is not None),
        default=datetime.now(tz=KST),
    )
    world = World(
        ev=ev,
        cat=cat,
        shelf=load_shelf_life(),
        presented=load_presented_flavors(PRESENTED_PATH),
        policy=RankingPolicy(),
        fallback_now=last + timedelta(days=1),
    )
    print(f"seed={args.seed_dir} users={len(users)} recipes={len(cat.recipes)}", end="")
    print(f" staples_in_catalog={len(cat.staple_ids)} fallback_now={world.fallback_now:%Y-%m-%d}")

    summaries = [summarize(world, user) for user in users.values()]
    invariants_ok = print_everyone(summaries)

    warm = max(
        (s for s in summaries if s.group == "sim_funnel_A" and s.candidates >= TOP_K),
        key=lambda s: s.behavior_weight,
        default=None,
    )
    if warm is None:
        print("\nA 집단에 후보가 충분한 유저가 없어 시나리오를 돌리지 못했습니다")
        print("\nRESULT: FAIL")
        return 1
    user = users[warm.user_id]
    now = world.now_of(user)
    first_mode, last_mode = timeline(world, user)
    converted = first_mode == UserMode.COLD.value and last_mode == UserMode.WARM.value
    print(f"    콜드 → 웜 전환: {converted} ({first_mode} → {last_mode})")
    behavior_ok = scenario_behavior(world, user, now)
    expiring_ok = scenario_expiring(world, user, now)
    peers = [s for s in summaries if s.group == user.group]
    repeat_ok = scenario_repeat(world, user, now, peers)

    verdict = {
        "invariants": invariants_ok,
        "cold_to_warm": converted,
        "behavior_moves_list": behavior_ok,
        "expiring_reaches_list": expiring_ok,
        "repeatable": repeat_ok,
    }
    print(f"\n판정 {verdict}")
    ok = all(verdict.values())
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
